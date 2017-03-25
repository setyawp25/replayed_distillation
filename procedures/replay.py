"""
This training procedure will train a student network from scratch using
replayed data sampled from a teacher model, and the sampled activations from
that teacher.
"""
import numpy as np
import os
import sys
import tensorflow as tf
import cv2
import scipy.stats as st
import scipy

import models as m
import utils as u

from utils import ensure_dir_exists

MODEL_META = 'summaries/hinton1200_mnist_withcollect/checkpoint/hinton1200-8000.meta'
MODEL_CHECKPOINT = 'summaries/hinton1200_mnist_withcollect/checkpoint/hinton1200-8000'

def merge_summary_list(summary_list, do_print=False):
    summary_dict = {}

    for summary in summary_list:
        summary_proto = tf.Summary()
        summary_proto.ParseFromString(summary)

        for val in summary_proto.value:
            if val.tag not in summary_dict:
                summary_dict[val.tag] = []
            summary_dict[val.tag].append(val.simple_value)

    # get mean of each tag
    for k, v in summary_dict.items():
        summary_dict[k] = np.mean(v)

    if do_print:
        print(summary_dict)

    # create final Summary with mean of values
    final_summary = tf.Summary()
    final_summary.ParseFromString(summary_list[0])

    for i, val in enumerate(final_summary.value):
        final_summary.value[i].simple_value = summary_dict[val.tag]

    return final_summary

def compute_class_statistics(sess, inp, keep_inp, keep, data, temp):
    all_activations = {}
    for batch_x, batch_y in data.train_epoch_in_batches(50):
        #  batch_out = sess.run('labels_sftmx/Reshape_1:0',
        batch_out = sess.run('784-1200-1200-10/temp/div:0',
                feed_dict={inp: batch_x, keep_inp: 1.0, keep: 1.0, 'temp_1:0': temp})

        for act, y in zip(batch_out, batch_y):
            clas = np.where(y == 1)[0][0]
            if clas not in all_activations:
                all_activations[clas] = []
            all_activations[clas].append(act)

    # consolidate them:
    means = {}
    cov = {}
    for k, v in all_activations.items():
        means[k] = np.mean(v, axis=0)
        cov[k] = np.cov(np.transpose(v))

    return means, cov

def sample_from_stats(stats, clas, batch_size, out_size):
    means, cov = stats
    out_size = means[list(means.keys())[0]].shape[0]
    gauss = np.random.normal(size=(batch_size, out_size))
    gauss = np.random.normal(size=(batch_size, out_size))
    return means[clas] + np.matmul(gauss, cov[clas])

def gkern(size=28, sig=4, noise=0.1):
    g = cv2.getGaussianKernel(size, sig)
    gi = cv2.getGaussianKernel(size, sig).T
    normd = np.matmul(g, gi)
    normd = (1.0 - noise) * (normd / normd.max())
    normd += noise * np.random.uniform(size=[size, size])
    return normd

def sample_images(sess, stats, clas, batch_size, input_placeholder,
        latent_placeholder, input_var, assign_op, recreate_op, data,
        latent_recreated, recreate_loss, reinit_op):

    # these hyper params can be tuned
    num_examples_per_median = 64
    noise = 0.1

    latent = np.zeros([10])
    latent[clas] = 1.0
    latent = [latent] * (num_examples_per_median)
    #  latent = sample_from_stats(stats, clas, 1, 10)
    #  latent = [latent[0]] * batch_size

    all_medians = []
    for i in range(batch_size):
        # currently setting noise to 0.1 and samples to 64, just bc that will make things easier rn
        sess.run(reinit_op)
        input_kernels = [np.reshape(gkern(noise=0.1), [784]) for _ in range(num_examples_per_median)]
        sess.run(assign_op, feed_dict={input_placeholder: input_kernels})
        for i in range(10000):
            _, los = sess.run([recreate_op, recreate_loss],
                    feed_dict={latent_placeholder: latent})
            #  if i < 25: print(los)
        all_medians.append(np.median(sess.run(input_var), axis=0))

    final_latent = np.zeros([10])
    final_latent[clas] = 1.0
    final_latent = [final_latent] * (batch_size)
    return all_medians, final_latent

def unblockshaped(arr, h, w):
    n, nrows, ncols = arr.shape
    return (arr.reshape(h//nrows, -1, nrows, ncols)
               .swapaxes(1,2)
               .reshape(h, w))

def reshape_to_row(arr):
    grid = np.array([np.reshape(img, (28, 28)) for img in arr])
    return unblockshaped(grid, int(28), int(28 * grid.shape[0]))

def reshape_to_grid(arr):
    grid = np.array([np.reshape(img, (28, 28)) for img in arr])
    size = int(28 * np.sqrt(grid.shape[0]))
    return unblockshaped(grid, size, size)

def compute_optimized_examples(sess, stats, train_batch_size,
        input_placeholder, latent_placeholder, input_var, assign_op,
        recreate_op, data, latent_recreated, recreate_loss, reinit_op):
    opt = {}
    for clas in range(10):
        print('clas: {}'.format(clas))
        opt[clas] = sample_images(sess, stats, clas, train_batch_size,
                input_placeholder, latent_placeholder, input_var, assign_op,
                recreate_op, data, latent_recreated, recreate_loss, reinit_op)
    return opt


def run(sess, f, data, placeholders, train_step, summary_op, summary_op_evaldistill):
    inp, labels, keep_inp, keep, temp, labels_temp, labels_evaldistill = placeholders

    # create same model with constants instead of vars. And a Variable as input
    with tf.variable_scope('const'):
        latent_placeholder = tf.placeholder(tf.float32, [None, 10], name='latent_placeholder')
        input_placeholder = tf.placeholder(tf.float32, [None, 784], name='input_placeholder')
        input_var = tf.Variable(tf.zeros([f.train_batch_size, 784]), name='recreated_imgs')
        sess.run(tf.variables_initializer([input_var], name='init_input'))
        assign_op = tf.assign(input_var, input_placeholder)
        latent_recreated = m.get('hinton1200').create_constant_model(sess, input_var)
        with tf.variable_scope('xent_recreated'):
            recreate_loss = tf.reduce_mean(
                    tf.nn.softmax_cross_entropy_with_logits(labels=latent_placeholder, logits=latent_recreated, name='sftmax_xent'))
        with tf.variable_scope('opt_recreated'):
            recreate_op = tf.train.AdamOptimizer(learning_rate=0.1).minimize(recreate_loss)

        reinit_op = tf.variables_initializer(u.get_uninitted_vars(sess), name='reinit_op')
        sess.run(reinit_op)

    saver = tf.train.Saver(tf.global_variables())

    summary_dir = os.path.join(f.summary_folder, f.run_name)
    train_writer = tf.summary.FileWriter(os.path.join(summary_dir, 'train'), sess.graph)
    trainbatch_writer = tf.summary.FileWriter(os.path.join(summary_dir, 'train_batch'), sess.graph)
    test_writer = tf.summary.FileWriter(os.path.join(summary_dir, 'test'), sess.graph)

    with sess.as_default():
        global_step = 0

        # step1: create dict of teacher model class statistics (as seen in Neurogenesis Deep Learning)
        stats = compute_class_statistics(sess, inp, keep_inp, keep, data, 8.0)
        print('optimizing data')
        data_optimized = compute_optimized_examples(sess, stats,
                f.train_batch_size, input_placeholder, latent_placeholder,
                input_var, assign_op, recreate_op, data, latent_recreated,
                recreate_loss, reinit_op)

        for k, v in data_optimized.items():
            cv2.imshow('grid', reshape_to_grid(v))
            cv2.waitKey(0)
            cv2.destroyAllWindows()

        for i in range(f.epochs):
            print('Epoch: {}'.format(i))
            for j in range(int(60000 / 64)):
                clas = j % 10
                batch_x, batch_y = data_optimized[clas]

                summary, _ = sess.run([summary_op_evaldistill, train_step],
                        feed_dict={inp: batch_x,
                            labels_evaldistill: batch_y,
                            keep_inp: 1.0, keep: 1.0,
                            'temp_1:0': 8.0, temp: 8.0})

                trainbatch_writer.add_summary(summary, global_step)

                if global_step % f.eval_interval == 0:
                    # eval test
                    summaries = []
                    for test_batch_x, test_batch_y in data.test_epoch_in_batches(f.test_batch_size):
                        summary = sess.run(summary_op_evaldistill,
                                feed_dict={inp: test_batch_x,
                                    labels_evaldistill: test_batch_y,
                                    keep_inp: 1.0, keep: 1.0,
                                    'temp_1:0': 1.0, temp: 1.0})
                        summaries.append(summary)
                    test_writer.add_summary(merge_summary_list(summaries, True), global_step)

                    # eval train
                    summaries = []
                    for train_batch_x, train_batch_y in data.train_epoch_in_batches(f.train_batch_size):
                        summary = sess.run(summary_op_evaldistill,
                                feed_dict={inp: train_batch_x,
                                    labels_evaldistill: train_batch_y,
                                    keep_inp: 1.0, keep: 1.0,
                                    'temp_1:0': 1.0, temp: 1.0})
                        summaries.append(summary)
                    train_writer.add_summary(merge_summary_list(summaries, True), global_step)

                global_step += 1

                if global_step % f.checkpoint_interval == 0:
                    checkpoint_dir = os.path.join(summary_dir, 'checkpoint/')
                    ensure_dir_exists(checkpoint_dir)
                    checkpoint_file = os.path.join(checkpoint_dir, f.model)
                    saver.save(sess, checkpoint_file, global_step=global_step)

def create_placeholders(sess, input_size, output_size, _):
    new_saver = tf.train.import_meta_graph(MODEL_META)
    new_saver.restore(sess, MODEL_CHECKPOINT)

    inp = tf.get_collection('input')[0]
    out = tf.get_collection('output')[0]
    keep_inp = tf.get_collection('keep_inp')[0]
    keep = tf.get_collection('keep')[0]
    labels_temp = tf.get_collection('labels_temp')[0]

    with tf.variable_scope('labels_sftmx'):
        labels = tf.nn.softmax(out)

    with tf.variable_scope('stoppp'):
        labels = tf.stop_gradient(labels)

    return inp, labels, keep_inp, keep, labels_temp
