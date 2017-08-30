import numpy as np

from tensorflow.examples.tutorials.mnist import input_data
from utils import grouper


class OptimizedDatasetIterator(object):

    def __init__(self, dataset_location):
        self.dataset_location = dataset_location.replace('<clas>', '{}').replace('<batch>', '{}')
        # save ioshape
        data_class_0 = np.load(self.dataset_location.format(0, 0))[()]
        # data_class_0 is the first batch for class = 0, as a tuple
        # data_class_0[0] is the first item in that tuple (batch_x)
        # data_class_0[0][0] is the first example in that batch
        # data_class_0[1] is the second item in that tuple (batch_y) which is a list of len 1
        # data_class_0[1][0] is the single element in that list, which is the actual batch of batch_size
        # data_class_0[1][0][0] is the first latent in that batch of latents
        self.input_size = len(data_class_0[0][0])
        self.output_size = len(data_class_0[1][0][0])

    @property
    def io_shape(self):
        return self.input_size, self.output_size

    def train_epoch_in_batches(self, _):
        classes_and_batches = [(clas_idx, batch_idx)
                for clas_idx in range(self.io_shape[1])
                for batch_idx  in range(len(self.data_optimized[0]))]
        np.random.shuffle(classes_and_batches)

        for clas_idx, batch_idx in classes_and_batches:
            yield np.load(self.dataset_location.format(clas_idx, batch_idx))[()]
