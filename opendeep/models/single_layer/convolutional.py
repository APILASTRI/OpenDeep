"""
.. module:: convolutional

This module provides the layers necessary for convolutional nets.

TO USE CUDNN WRAPPING, YOU MUST INSTALL THE APPROPRIATE .h and .so FILES FOR THEANO LIKE SO:
http://deeplearning.net/software/theano/library/sandbox/cuda/dnn.html
"""

__authors__ = "Markus Beissinger"
__copyright__ = "Copyright 2015, Vitruvian Science"
__credits__ = ["Lasagne", "Weiguang Ding", "Ruoyan Wang", "Fei Mao", "Graham Taylor", "Markus Beissinger"]
__license__ = "Apache"
__maintainer__ = "OpenDeep"
__email__ = "opendeep-dev@googlegroups.com"

# standard libraries
import logging
# third party libraries
import numpy
import theano
import theano.tensor as T
from theano.tensor.signal import downsample
# internal references
from opendeep.models.model import Model
from opendeep.utils.activation import get_activation_function
from opendeep.utils.nnet import get_weights_gaussian, get_weights_uniform, get_bias, cross_channel_normalization_bc01
from opendeep.utils.activation import rectifier
from opendeep.utils.conv1d_implementations import conv1d_mc0


log = logging.getLogger(__name__)

# flag for having NVIDIA's CuDNN library.
has_cudnn = True
try:
    from theano.sandbox.cuda import dnn
except ImportError, e:
    has_cudnn = False
    log.warning("Could not import CuDNN from theano. For fast convolutions, "
                "please install it like so: http://deeplearning.net/software/theano/library/sandbox/cuda/dnn.html")

# Some convolution operations only work on the GPU, so do a check here:
if not theano.config.device.startswith('gpu'):
    log.warning("You should reeeeeaaaally consider using a GPU, unless this is a small toy algorithm for fun. "
                "Please enable the GPU in Theano via these instructions: "
                "http://deeplearning.net/software/theano/tutorial/using_gpu.html")

# To use the fastest convolutions possible, need to set the Theano flag as described here:
# http://benanne.github.io/2014/12/09/theano-metaopt.html
# make it THEANO_FLAGS=optimizer_including=conv_meta
# OR you could set the .theanorc file with [global]optimizer_including=conv_meta
if theano.config.optimizer_including != "conv_meta":
    log.warning("Theano flag optimizer_including is not conv_meta (found %s)! "
                "To have Theano cherry-pick the best convolution implementation, please set "
                "optimizer_including=conv_meta either in THEANO_FLAGS or in the .theanorc file!"
                % str(theano.config.optimizer_including))


class Conv1D(Model):
    """
    A 1-dimensional convolution (taken from Sander Dieleman's Lasagne framework)
    (https://github.com/benanne/Lasagne/blob/master/lasagne/theano_extensions/conv.py)
    """
    defaults = {
        "border_mode": "valid",
        "weights_init": "uniform",
        'weights_interval': 'montreal',  # if the weights_init was 'uniform', how to initialize from uniform
        'weights_mean': 0,  # mean for gaussian weights init
        'weights_std': 0.005,  # standard deviation for gaussian weights init
        'bias_init': 0.0,  # how to initialize the bias parameter
        "activation": rectifier,
        "convolution": conv1d_mc0
    }
    def __init__(self, inputs_hook, params_hook=None, input_shape=None, filter_shape=None, stride=None,
                 weights_init=None, weights_interval=None, weights_mean=None, weights_std=None, bias_init=None,
                 border_mode=None, activation=None, convolution=None, config=None, defaults=defaults):
        super(Conv1D, self).__init__(config=config, defaults=defaults)
        # configs can now be accessed through self.args

        ##################
        # specifications #
        ##################
        # grab info from the inputs_hook, or from parameters
        # expect input to be in the form (B, C, I) (batch, channel, input data)
        #  inputs_hook is a tuple of (Shape, Input)
        if inputs_hook:
            # make sure inputs_hook is a tuple
            assert len(inputs_hook) == 2, "expecting inputs_hook to be tuple of (shape, input)"
            input_shape = inputs_hook[0] or input_shape or self.args.get('input_shape')
            self.input = inputs_hook[1]
        else:
            # either grab from the parameter directly or self.args config
            input_shape = input_shape or self.args.get('input_shape')
            # make the input a symbolic matrix
            self.input = T.ftensor3('X')

        # activation function!
        activation_name = activation or self.args.get('activation')
        if isinstance(activation_name, basestring):
            activation_func = get_activation_function(activation_name)
        else:
            activation_func = activation_name
            assert callable(activation_name), "Activation function either needs to be a string name or callable!"

        # filter shape should be in the form (num_filters, num_channels, filter_length)
        filter_shape = filter_shape or self.args.get('filter_shape')
        num_filters = filter_shape[0]
        filter_length = filter_shape[2]
        stride = stride or self.args.get('stride')
        border_mode = border_mode or self.args.get('border_mode')
        convolution = convolution or self.args.get('convolution')

        weights_init = weights_init or self.args.get('weights_init')
        weights_interval = weights_interval or self.args.get('weights_interval')
        weights_mean = weights_mean or self.args.get('weights_mean')
        weights_std = weights_std or self.args.get('weights_std')

        ################################################
        # Params - make sure to deal with params_hook! #
        ################################################
        if params_hook:
            # make sure the params_hook has W and b
            assert len(params_hook) == 2, "Expected 2 params (W and b) for Conv1D, found {0!s}!".format(
                len(params_hook))
            W, b = params_hook
        else:
            # if we are initializing weights from a gaussian
            if weights_init.lower() == 'gaussian':
                W = get_weights_gaussian(shape=filter_shape, mean=weights_mean, std=weights_std, name="W")
            # if we are initializing weights from a uniform distribution
            elif self.args.get('weights_init').lower() == 'uniform':
                W = get_weights_uniform(shape=filter_shape, interval=weights_interval, name="W")
            # otherwise not implemented
            else:
                log.error("Did not recognize weights_init %s! Pleas try gaussian or uniform" %
                          str(self.args.get('weights_init')))
                raise NotImplementedError(
                    "Did not recognize weights_init %s! Pleas try gaussian or uniform" %
                    str(self.args.get('weights_init')))

            b = get_bias(shape=(num_filters,), name="b", init_values=bias_init)

        # Finally have the two parameters!
        self.params = [W, b]

        ########################
        # Computational Graph! #
        ########################
        if border_mode in ['valid', 'full']:
            conved = convolution(self.input,
                                 W,
                                 subsample=(stride,),
                                 image_shape=input_shape,
                                 filter_shape=filter_shape,
                                 border_mode=border_mode)
        elif border_mode == 'same':
            conved = convolution(self.input,
                                 W,
                                 subsample=(stride,),
                                 image_shape=input_shape,
                                 filter_shape=filter_shape,
                                 border_mode='full')
            shift = (filter_length - 1) // 2
            conved = conved[:, :, shift:input_shape[2] + shift]

        else:
            log.error("Invalid border mode: '%s'" % border_mode)
            raise RuntimeError("Invalid border mode: '%s'" % border_mode)

        self.output = activation_func(conved + b.dimshuffle('x', 0, 'x'))

    def get_inputs(self):
        return [self.input]

    def get_outputs(self):
        return self.output

    def get_params(self):
        return self.params


class Conv2D(Model):
    """
    A 2-dimensional convolution (taken from Sander Dieleman's Lasagne framework)
    (https://github.com/benanne/Lasagne/blob/master/lasagne/theano_extensions/conv.py)
    """
    defaults = {
        "border_mode": "valid",
        "weights_init": "uniform",
        'weights_interval': 'montreal',  # if the weights_init was 'uniform', how to initialize from uniform
        'weights_mean': 0,  # mean for gaussian weights init
        'weights_std': 0.005,  # standard deviation for gaussian weights init
        'bias_init': 0.0,  # how to initialize the bias parameter
        "activation": rectifier,
        # using the theano flag optimizer_including=conv_meta will let this conv function optimize itself.
        "convolution": T.nnet.conv2d
    }
    def __init__(self, inputs_hook, params_hook=None, input_shape=None, filter_shape=None, strides=None,
                 weights_init=None, weights_interval=None, weights_mean=None, weights_std=None, bias_init=None,
                 border_mode=None, activation=None, convolution=None, config=None, defaults=defaults):
        super(Conv2D, self).__init__(config=config, defaults=defaults)
        # configs can now be accessed through self.args

        ##################
        # specifications #
        ##################
        # grab info from the inputs_hook, or from parameters
        # expect input to be in the form (B, C, 0, 1) (batch, channel, rows, cols)
        # inputs_hook is a tuple of (Shape, Input)
        if inputs_hook:
            # make sure inputs_hook is a tuple
            assert len(inputs_hook) == 2, "expecting inputs_hook to be tuple of (shape, input)"
            input_shape = inputs_hook[0] or input_shape or self.args.get('input_shape')
            self.input = inputs_hook[1]
        else:
            # either grab from the parameter directly or self.args config
            input_shape = input_shape or self.args.get('input_shape')
            # make the input a symbolic matrix
            self.input = T.ftensor4('X')

        # activation function!
        activation_name = activation or self.args.get('activation')
        if isinstance(activation_name, basestring):
            activation_func = get_activation_function(activation_name)
        else:
            activation_func = activation_name
            assert callable(activation_name), "Activation function either needs to be a string name or callable!"

        # filter shape should be in the form (num_filters, num_channels, filter_size[0], filter_size[1])
        filter_shape = filter_shape or self.args.get('filter_shape')
        num_filters = filter_shape[0]
        filter_size = filter_shape[2:3]
        strides = strides or self.args.get('strides')
        border_mode = border_mode or self.args.get('border_mode')
        convolution = convolution or self.args.get('convolution')

        weights_init = weights_init or self.args.get('weights_init')
        weights_interval = weights_interval or self.args.get('weights_interval')
        weights_mean = weights_mean or self.args.get('weights_mean')
        weights_std = weights_std or self.args.get('weights_std')

        ################################################
        # Params - make sure to deal with params_hook! #
        ################################################
        if params_hook:
            # make sure the params_hook has W and b
            assert len(params_hook) == 2, "Expected 2 params (W and b) for Conv2D, found {0!s}!".format(
                len(params_hook))
            W, b = params_hook
        else:
            # if we are initializing weights from a gaussian
            if weights_init.lower() == 'gaussian':
                W = get_weights_gaussian(shape=filter_shape, mean=weights_mean, std=weights_std, name="W")
            # if we are initializing weights from a uniform distribution
            elif self.args.get('weights_init').lower() == 'uniform':
                W = get_weights_uniform(shape=filter_shape, interval=weights_interval, name="W")
            # otherwise not implemented
            else:
                log.error("Did not recognize weights_init %s! Pleas try gaussian or uniform" %
                          str(self.args.get('weights_init')))
                raise NotImplementedError(
                    "Did not recognize weights_init %s! Pleas try gaussian or uniform" %
                    str(self.args.get('weights_init')))

            b = get_bias(shape=(num_filters, ), name="b", init_values=bias_init)

        # Finally have the two parameters!
        self.params = [W, b]

        ########################
        # Computational Graph! #
        ########################
        if border_mode in ['valid', 'full']:
            conved = convolution(self.input,
                                 W,
                                 subsample=strides,
                                 image_shape=input_shape,
                                 filter_shape=filter_shape,
                                 border_mode=border_mode)
        elif border_mode == 'same':
            conved = convolution(self.input,
                                 W,
                                 subsample=strides,
                                 image_shape=input_shape,
                                 filter_shape=filter_shape,
                                 border_mode='full')
            shift_x = (filter_size[0] - 1) // 2
            shift_y = (filter_size[1] - 1) // 2
            conved = conved[:, :, shift_x:input_shape[2] + shift_x,
                            shift_y:input_shape[3] + shift_y]
        else:
            raise RuntimeError("Invalid border mode: '%s'" % border_mode)

        self.output = activation_func(conved + b.dimshuffle('x', 0, 'x', 'x'))

    def get_inputs(self):
        return [self.input]

    def get_outputs(self):
        return self.output

    def get_params(self):
        return self.params


class Conv3D(Model):
    """
    A 3-dimensional convolution layer
    """
    defaults = {
        "border_mode": "valid",
        "weights_init": "uniform",
        'weights_interval': 'montreal',  # if the weights_init was 'uniform', how to initialize from uniform
        'weights_mean': 0,  # mean for gaussian weights init
        'weights_std': 0.005,  # standard deviation for gaussian weights init
        'bias_init': 0.0,  # how to initialize the bias parameter
        "activation": rectifier,
        # using the theano flag optimizer_including=conv_meta will let this conv function optimize itself.
        "convolution": T.nnet.conv3D
    }

    def __init__(self):
        log.error("Conv3D not implemented yet.")
        super(Conv3D, self).__init__()
        raise NotImplementedError("Conv3D not implemented yet.")


class ConvPoolLayer(Model):
    """
    This is the ConvPoolLayer used for an AlexNet implementation.

    Copyright (c) 2014, Weiguang Ding, Ruoyan Wang, Fei Mao and Graham Taylor
    All rights reserved.
    Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:
        1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
        2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
        3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.
    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

    """
    defaults = {
        'filter_shape': (96, 3, 11, 11),  # bc01
        'convstride': 4,
        'padsize': 0,
        'group': 1,
        'poolsize': 3,
        'poolstride': 2,
        'bias_init': 0,
        'local_response_normalization': False,
        'convolution': T.nnet.conv2d,
        'activation': 'rectifier'
    }
    def __init__(self, inputs_hook, input_shape=None, filter_shape=None, convstride=None, padsize=None, group=None,
                 poolsize=None, poolstride=None, bias_init=None, local_response_normalization=None,
                 convolution=None, activation=None, params_hook=None, config=None, defaults=defaults):
        # init Model to combine the defaults and config dictionaries.
        super(ConvPoolLayer, self).__init__(config, defaults)
        # all configuration parameters are now in self.args

        # deal with the inputs coming from inputs_hook - necessary for now to give an input hook
        # inputs_hook is a tuple of (Shape, Input)
        if inputs_hook:
            assert len(inputs_hook) == 2, "expecting inputs_hook to be tuple of (shape, input)"
            self.input_shape = inputs_hook[0] or input_shape or self.args.get('input_shape')
            self.input = inputs_hook[1]
        else:
            self.input_shape = input_shape or self.args.get('input_shape')
            self.input = T.ftensor4("X")

        #######################
        # layer configuration #
        #######################
        # activation function!
        activation_name = activation or self.args.get('activation')
        if isinstance(activation_name, basestring):
            self.activation_func = get_activation_function(activation_name)
        else:
            self.activation_func = activation_name
            assert callable(activation_name), "Activation function either needs to be a string name or callable!"
        self.convolution = convolution or self.args.get('convolution')
        self.filter_shape = filter_shape or self.args.get('filter_shape')
        self.convstride = convstride or self.args.get('convstride')
        self.padsize = padsize or self.args.get('padsize')

        self.poolsize = poolsize or self.args.get('poolsize')
        self.poolstride = poolstride or self.args.get('poolstride')

        # expect image_shape to be bc01!
        self.channel = self.input_shape[1]

        self.lrn = local_response_normalization or self.args.get('local_response_normalization')

        # if lib_conv is cudnn, it works only on square images and the grad works only when channel % 16 == 0

        self.group = group or self.args.get('group')
        assert self.group in [1, 2], "group argument needs to be 1 or 2 (1 for default conv2d)"

        self.filter_shape = numpy.asarray(self.filter_shape)
        self.input_shape = numpy.asarray(self.input_shape)

        if self.lrn:
            self.lrn_func = cross_channel_normalization_bc01

        ################################################
        # Params - make sure to deal with params_hook! #
        ################################################
        if self.group == 1:
            if params_hook:
                # make sure the params_hook has W and b
                assert len(params_hook) == 2, "Expected 2 params (W and b) for ConvPoolLayer, found {0!s}!".format(
                    len(params_hook))
                self.W, self.b = params_hook
            else:
                self.W = get_weights_gaussian(shape=self.filter_shape, mean=0, std=0.01, name="W")
                self.b = get_bias(shape=self.filter_shape[0], init_values=bias_init, name="b")
            self.params = [self.W, self.b]
        else:
            self.filter_shape[0] = self.filter_shape[0] / 2
            self.filter_shape[1] = self.filter_shape[1] / 2

            self.input_shape[0] = self.input_shape[0] / 2
            self.input_shape[1] = self.input_shape[1] / 2
            if params_hook:
                assert len(params_hook) == 4
                self.W0, self.W1, self.b0, self.b1 = params_hook
            else:
                self.W0 = get_weights_gaussian(shape=self.filter_shape, name="W0")
                self.W1 = get_weights_gaussian(shape=self.filter_shape, name="W1")
                self.b0 = get_bias(shape=self.filter_shape[0], init_values=bias_init, name="b0")
                self.b1 = get_bias(shape=self.filter_shape[0], init_values=bias_init, name="b1")
            self.params = [self.W0, self.b0, self.W1, self.b1]

        #############################################
        # build appropriate graph for conv. version #
        #############################################
        self.output = self.build_computation_graph()

        # Local Response Normalization (for AlexNet)
        if self.lrn:
            self.output = self.lrn_func(self.output)

        log.debug("conv layer initialized with shape_in: %s", str(self.input_shape))

    def build_computation_graph(self):
        if self.group == 1:
            conv_out = self.convolution(img=self.input,
                                        kerns=self.W,
                                        subsample=(self.convstride, self.convstride),
                                        border_mode=(self.padsize, self.padsize))
            conv_out = conv_out + self.b.dimshuffle('x', 0, 'x', 'x')

        else:
            conv_out0 = self.convolution(img=self.input[:, :self.channel / 2, :, :],
                                         kerns=self.W0,
                                         subsample=(self.convstride, self.convstride),
                                         border_mode=(self.padsize, self.padsize))
            conv_out0 = conv_out0 + self.b0.dimshuffle('x', 0, 'x', 'x')


            conv_out1 = self.convolution(img=self.input[:, self.channel / 2:, :, :],
                                         kerns=self.W1,
                                         subsample=(self.convstride, self.convstride),
                                         border_mode=(self.padsize, self.padsize))
            conv_out1 = conv_out1 + self.b1.dimshuffle('x', 0, 'x', 'x')

            conv_out = T.concatenate([conv_out0, conv_out1], axis=1)

        # ReLu by default
        output = self.activation_func(conv_out)

        # Pooling
        if self.poolsize != 1:
            if has_cudnn:
                output = dnn.dnn_pool(output,
                                      ws=(self.poolsize, self.poolsize),
                                      stride=(self.poolstride, self.poolstride))
            else:
                output = downsample.max_pool_2d(output,
                                                ds=(self.poolsize, self.poolsize),
                                                st=(self.poolstride, self.poolstride))

        return output

    def get_inputs(self):
        return [self.input]

    def get_outputs(self):
        return self.output

    def get_params(self):
        return self.params