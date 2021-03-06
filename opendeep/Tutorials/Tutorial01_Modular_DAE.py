"""
Please refer to the following tutorials in the documentation at www.opendeep.org

Tutorial: Your First Model (DAE)
Tutorial: Flexibility and Modularity
"""
import theano.tensor as T
from opendeep import function
from opendeep.models.model import Model
from opendeep.utils.nnet import get_weights_uniform, get_bias
from opendeep.utils.noise import salt_and_pepper
from opendeep.utils.activation import get_activation_function
from opendeep.utils.cost import get_cost_function

# create our class initialization!
class DenoisingAutoencoder(Model):
    """
    A denoising autoencoder will corrupt an input (add noise) and try to reconstruct it.
    """
    # Best practice to define all the default parameters up here
    # Provide comments when giving parameters so people know what they are for!
    _defaults = {
        "input_size": 28*28, # dimensionality of input - works for MNIST
        "hidden_size": 1000, # number of hidden units
        "corruption_level": 0.4, # how much noise to add to the input
        "hidden_activation": 'tanh', # the nonlinearity to apply to hidden units
        "visible_activation": 'sigmoid', # the nonlinearity to apply to visible units
        "cost_function": 'binary_crossentropy' # the cost function to use during training
    }
    def __init__(self, config=None, defaults=_defaults,
                 inputs_hook=None, hiddens_hook=None, params_hook=None,
                 input_size=None, hidden_size=None, corruption_level=None,
                 hidden_activation=None, visible_activation=None, cost_function=None):
        # Now, initialize with Model class to combine config and defaults!
        # Here, defaults is defined via a dictionary. However, you could also
        # pass a filename to a JSON or YAML file with the same format.
        super(DenoisingAutoencoder, self).__init__(config=config, defaults=defaults)
        # Any parameter from the 'config' will overwrite the 'defaults' dictionary.
        # These parameters are now accessible from the 'self.args' variable!

        # When accessing model parameters, it is best practice to try to find the parameters
        # explicitly passed first, and then go to the 'self.args' configuration.

        # Define model hyperparameters
        # deal with the inputs_hook and hiddens_hook for the size parameters!
        # if the hook exists, grab the size from the first element of the tuple.
        if inputs_hook:
            input_size = inputs_hook[0]
        # otherwise, grab the size from the configurations.
        else:
            input_size = input_size or self.args.get('input_size')
        if hiddens_hook:
            hidden_size = hiddens_hook[0]
        else:
            hidden_size = hidden_size or self.args.get('hidden_size')

        corruption_level = corruption_level or self.args.get('corruption_level')

        # use the helper methods to grab appropriate activation functions from names!
        hidden_act_name    = hidden_activation or self.args.get('hidden_activation')
        hidden_activation  = get_activation_function(hidden_act_name)
        visible_act_name   = visible_activation or self.args.get('visible_activation')
        visible_activation = get_activation_function(visible_act_name)

        # do the same for the cost function
        cost_func_name = cost_function or self.args.get('cost_function')
        cost_function  = get_cost_function(cost_func_name)

        # Now, define the symbolic input to the model (Theano)
        # We use a matrix rather than a vector so that minibatch processing can be done in parallel.
        # Make sure to deal with 'inputs_hook' if it exists!
        if inputs_hook:
            # grab the new input variable from the inputs_hook tuple
            x = inputs_hook[1]
        else:
            x = T.fmatrix("X")
        self.inputs = [x]

        # Build the model's parameters - a weight matrix and two bias vectors
        # Make sure to deal with 'params_hook' if it exists!
        if params_hook:
            # check to see if it contains the three necessary variables
            assert len(params_hook) == 3, "Not correct number of params to DAE, needs 3!"
            W, b0, b1 = params_hook
        else:
            W  = get_weights_uniform(shape=(input_size, hidden_size), name="W")
            b0 = get_bias(shape=input_size, name="b0")
            b1 = get_bias(shape=hidden_size, name="b1")
        self.params = [W, b0, b1]

        # Perform the computation for a denoising autoencoder!
        # first, add noise (corrupt) the input
        corrupted_input = salt_and_pepper(input=x, corruption_level=corruption_level)
        # next, compute the hidden layer given the inputs (the encoding function)
        # We don't need to worry about hiddens_hook during training, because we can't
        # compute a cost without having the input!
        # hiddens_hook is more for the predict function and linking methods below.
        hiddens = hidden_activation(T.dot(corrupted_input, W) + b1)
        # finally, create the reconstruction from the hidden layer (we tie the weights with W.T)
        reconstruction = visible_activation(T.dot(hiddens, W.T) + b0)
        # the training cost is reconstruction error
        self.train_cost = cost_function(output=reconstruction, target=x)

        # Compile everything into a Theano function for prediction!
        # When using real-world data in predictions, we wouldn't corrupt the input first.
        # Therefore, create another version of the hiddens and reconstruction without adding the noise.
        # Here is where we would handle hiddens_hook because this is a generative model!
        # For the predict function, it would take in the hiddens instead of the input variable x.
        if hiddens_hook:
            self.hiddens = hiddens_hook[1]
        else:
            self.hiddens = hidden_activation(T.dot(x, W) + b1)
        # make the reconstruction (generated) from the hiddens
        self.recon_predict = visible_activation(T.dot(self.hiddens, W.T) + b0)
        # now compile the predict function accordingly - if it used x or hiddens as the input.
        if hiddens_hook:
            self.f_predict = function(inputs=[self.hiddens], outputs=self.recon_predict)
        else:
            self.f_predict = function(inputs=[x], outputs=self.recon_predict)

    def get_inputs(self):
        return self.inputs

    def get_hiddens(self):
        return self.hiddens

    def get_outputs(self):
        return self.recon_predict

    def get_params(self):
        return self.params

    def get_train_cost(self):
        return self.train_cost

    def predict(self, input):
        return self.f_predict(input)

if __name__ == '__main__':
    # set up the logging environment to display outputs (optional)
    # although this is recommended over print statements everywhere
    import logging
    import opendeep.log.logger as logger
    logger.config_root_logger()
    log = logging.getLogger(__name__)
    log.info("Creating a Denoising Autoencoder!")

    # import the dataset and optimizer to use
    from opendeep.data.dataset import TEST
    from opendeep.data.standard_datasets.image.mnist import MNIST
    from opendeep.optimization.adadelta import AdaDelta

    # grab the MNIST dataset
    mnist = MNIST()

    # create your shiny new DAE
    dae = DenoisingAutoencoder()

    # make an optimizer to train it (AdaDelta is a good default)
    optimizer = AdaDelta(model=dae, dataset=mnist)
    # perform training!
    optimizer.train()

    # test it on some images!
    test_data = mnist.getDataByIndices(indices=range(25), subset=TEST)
    corrupted_test = salt_and_pepper(test_data, 0.4)
    # use the predict function!
    reconstructed_images = dae.predict(test_data)

    # create an image from this reconstruction!
    # imports for working with tiling outputs into one image
    from opendeep.utils.image import tile_raster_images
    import numpy
    import PIL
    # stack the image matrices together in three 5x5 grids next to each other using numpy
    stacked = numpy.vstack(
        [numpy.vstack([test_data[i*5 : (i+1)*5],
                       corrupted_test.eval()[i*5 : (i+1)*5],
                       reconstructed_images[i*5 : (i+1)*5]])
         for i in range(5)])
    # convert the combined matrix into an image
    image = PIL.Image.fromarray(
        tile_raster_images(stacked, (28, 28), (5, 3*5))
    )
    # save it!
    image.save("dae_reconstruction_test.png")