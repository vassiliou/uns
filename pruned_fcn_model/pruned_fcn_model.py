from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf
#from . import fcn_input
#from . import fcn16_vgg
#from .fcn16_vgg import FCN16VGG

import pruned_fcn_input
import pruned_fcn16_vgg
from pruned_fcn16_vgg import FCN16VGG

FLAGS = tf.app.flags.FLAGS

# Basic model parameters.
tf.app.flags.DEFINE_integer('batch_size', 1,
                            """Number of records to process in a batch.""")
tf.app.flags.DEFINE_string('data_dir', '/Users/gus/CDIPS/bottleneck_files',
                           """Path to the input data directory.""")

#tf.app.flags.DEFINE_string('vgg_path','/Users/gus/CDIPS/uns/fcn_model/vgg16.npy',"""Path to the file containing vgg weights""")

# Global constants describing the data .

NUM_CLASSES = pruned_fcn_input.NUM_CLASSES
PREDICTION_SHAPE = [416,576,NUM_CLASSES]
NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN = pruned_fcn_input.NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN
NUM_EXAMPLES_PER_EPOCH_FOR_EVAL = pruned_fcn_input.NUM_EXAMPLES_PER_EPOCH_FOR_EVAL



# Constants describing the training process.
MOVING_AVERAGE_DECAY = 0.9999     # The decay to use for the moving average.
NUM_EPOCHS_PER_DECAY = 350.0      # Epochs after which learning rate decays.
LEARNING_RATE_DECAY_FACTOR = 0.1  # Learning rate decay factor.
INITIAL_LEARNING_RATE = 10e-6       # Initial learning rate.


FC7_SHAPE=[4096,512]
FC8_SHAPE=[512,256]

def _activation_summary(x):
  """Helper to create summaries for activations.

  Creates a summary that provides a histogram of activations.
  Creates a summary that measure the sparsity of activations.

  Args:
    x: Tensor
  Returns:
    nothing
  """
  # Remove 'tower_[0-9]/' from the name in case this is a multi-GPU training
  # session. This helps the clarity of presentation on tensorboard.
  tensor_name = x.op.name
  tf.histogram_summary(tensor_name + '/activations', x)
  tf.scalar_summary(tensor_name + '/sparsity', tf.nn.zero_fraction(x))

  
def _variable_with_weight_decay(name, shape, stddev, wd):
  """Helper to create an initialized Variable with weight decay.

  Note that the Variable is initialized with a truncated normal distribution.
  A weight decay is added only if one is specified.

  Args:
    name: name of the variable
    shape: list of ints
    stddev: standard deviation of a truncated Gaussian
    wd: add L2Loss weight decay multiplied by this float. If None, weight
        decay is not added for this Variable.

  Returns:
    Variable Tensor
  """
  var = tf.Variable(tf.truncated_normal_initializer(shape,stddev=stddev),name=name)
  if wd:
    weight_decay = tf.mul(tf.nn.l2_loss(var), wd, name='weight_loss')
    tf.add_to_collection('losses', weight_decay)
  return var


class bottledFCN16(FCN16VGG):
    global PREDICTION_SHAPE
    
    def __init__(self,vgg_path = '/Users/gus/CDIPS/vgg16.npy'):
        FCN16VGG.__init__(self,vgg_path)

    def build(self, inputs,train = False,num_classes=2, random_init_fc8=False,debug=False):
        """ argument inputs is tuple of (fc6_output, pool4_output)  """

        output_shape = tf.TensorShape([None]+PREDICTION_SHAPE)
        
        self.fc7 = self._pruned_fc(inputs[0],FC7_SHAPE,[1,1,4096,4096],'fc7')
        if train:
            self.fc7 = tf.nn.dropout(self.fc7, 0.5)

        if random_init_fc8:
            self.score_fr = self._score_layer(self.fc7, "score_fr",
                                              FC8_SHAPE[1])
        else:
            self.score_fr = self._pruned_fc(self.fc6,FC8_SHAPE,[1,1,4096,4096],'fc8')

        self.pred = tf.argmax(self.score_fr, dimension=3)

        self.upscore2 = self._upscore_layer(self.score_fr,
                                            shape=tf.shape(inputs[1]),
                                            num_classes=num_classes,
                                            debug=debug, name='upscore2',
                                            ksize=4, stride=2)

        self.score_pool4 = self._score_layer(inputs[1], "score_pool4",
                                             num_classes=num_classes)

        self.fuse_pool4 = tf.add(self.upscore2, self.score_pool4)

        
        
        self.upscore32 = self._upscore_layer(self.fuse_pool4,
                                             shape=tf.shape(inputs[2]),
                                             num_classes=num_classes,
                                             debug=debug, name='upscore32',
                                             ksize=32, stride=16)

        self.pred_up = tf.argmax(self.upscore32, dimension=3)

def inputs():
    if not FLAGS.data_dir:
          raise ValueError('Please supply a data_dir')
    data_dir = FLAGS.data_dir
    return pruned_fcn_input.inputs(data_dir=data_dir,
                                        batch_size=FLAGS.batch_size)


### helpers to build layers


def inference(inputs):
  """Build our MNIST model.

  Args:
    images: Images returned from distorted_inputs() or inputs().

  Returns:
    Logits.
  """
  # We instantiate all variables using tf.get_variable() instead of
  # tf.Variable() in order to share variables across multiple GPU training runs.
  # If we only ran this model on a single GPU, we could simplify this function
  # by replacing all instances of tf.get_variable() with tf.Variable().
  #
  # conv1

  net = bottledFCN16()
  
  with tf.name_scope('vgg_net') as scope:
    net.build(inputs,train=False,num_classes=2,random_init_fc8=True)
  
  return (net.upscore32,net.fc7,net.score_fr)


def loss(logits, labels, num_classes):
    """Calculate the loss from the logits and the labels.

    Args:
      logits: tensor, float - [batch_size, width, height, num_classes].
          Use vgg_fcn.up as logits.
      labels: Labels tensor, int32 - [batch_size, width, height, num_classes].
          The ground truth of your data.
      head: numpy array - [num_classes]
          Weighting the loss of each class
          Optional: Prioritize some classes

    Returns:
      loss: Loss tensor of type float.
    """


    with tf.name_scope('loss'):
        logits = tf.reshape(logits, (-1, num_classes))
        epsilon = tf.constant(value=1e-4)
        logits = logits + epsilon
        labels = tf.to_float(tf.reshape(labels, (-1, num_classes)))

        softmax = tf.nn.softmax(logits)

        cross_entropy = -tf.reduce_sum(labels * tf.log(softmax), reduction_indices=[1])

        cross_entropy_mean = tf.reduce_mean(cross_entropy,
                                            name='xentropy_mean')
        tf.add_to_collection('losses', cross_entropy_mean)

        loss = tf.add_n(tf.get_collection('losses'), name='total_loss')
    return loss



def _add_loss_summaries(total_loss):
  """Add summaries for losses in model.

  Generates moving average for all losses and associated summaries for
  visualizing the performance of the network.

  Args:
    total_loss: Total loss from loss().
  Returns:
    loss_averages_op: op for generating moving averages of losses.
  """
  # Compute the moving average of all individual losses and the total loss.
  loss_averages = tf.train.ExponentialMovingAverage(0.9, name='avg')
  losses = tf.get_collection('losses')
  loss_averages_op = loss_averages.apply(losses + [total_loss])

  # Attach a scalar summary to all individual losses and the total loss; do the
  # same for the averaged version of the losses.
  for l in losses + [total_loss]:
    # Name each loss as '(raw)' and name the moving average version of the loss
    # as the original loss name.
    tf.scalar_summary(l.op.name +' (raw)', l)
    tf.scalar_summary(l.op.name, loss_averages.average(l))

  return loss_averages_op


def train(total_loss, global_step):
  """Train the model.

  Create an optimizer and apply to all trainable variables. Add moving
  average for all trainable variables.

  Args:
    total_loss: Total loss from loss().
    global_step: Integer Variable counting the number of training steps
      processed.
  Returns:
    train_op: op for training.
  """
  # Variables that affect learning rate.
  num_batches_per_epoch = NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN / FLAGS.batch_size
  decay_steps = int(num_batches_per_epoch * NUM_EPOCHS_PER_DECAY)

  # Decay the learning rate exponentially based on the number of steps.
  lr = tf.train.exponential_decay(INITIAL_LEARNING_RATE,
                                  global_step,
                                  decay_steps,
                                  LEARNING_RATE_DECAY_FACTOR,
                                  staircase=True)
  tf.scalar_summary('learning_rate', lr)

  # Generate moving averages of all losses and associated summaries.
  loss_averages_op = _add_loss_summaries(total_loss)

  # Compute gradients.
  with tf.control_dependencies([loss_averages_op]):
    opt = tf.train.AdamOptimizer(lr)
    grads = opt.compute_gradients(total_loss)

  # Apply gradients.
  apply_gradient_op = opt.apply_gradients(grads, global_step=global_step)

  # Add histograms for trainable variables.
  for var in tf.trainable_variables():
    tf.histogram_summary(var.op.name, var)

  # Add histograms for gradients.
  #for grad, var in grads:
   # if grad is not None:
   #   tf.histogram_summary(var.op.name + '/gradients', grad)

  # Track the moving averages of all trainable variables.
  variable_averages = tf.train.ExponentialMovingAverage(
      MOVING_AVERAGE_DECAY, global_step)
  variables_averages_op = variable_averages.apply(tf.trainable_variables())

  with tf.control_dependencies([apply_gradient_op, variables_averages_op]):
    train_op = tf.no_op(name='train')

  return train_op
