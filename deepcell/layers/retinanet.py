# Copyright 2016-2019 The Van Valen Lab at the California Institute of
# Technology (Caltech), with support from the Paul Allen Family Foundation,
# Google, & National Institutes of Health (NIH) under Grant U24CA224309-01.
# All rights reserved.
#
# Licensed under a modified Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.github.com/vanvalenlab/tf-keras-retinanet/LICENSE
#
# The Work provided may be used for non-commercial academic purposes only.
# For any other use of the Work, including commercial use, please contact:
# vanvalenlab@gmail.com
#
# Neither the name of Caltech nor the names of its contributors may be used
# to endorse or promote products derived from this software without specific
# prior written permission.
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""RetinaNet layers adapted from https://github.com/fizyr/keras-retinanet"""
from __future__ import absolute_import
from __future__ import print_function
from __future__ import division

import numpy as np
import tensorflow as tf
from tensorflow.python.framework import tensor_shape
from tensorflow.python.keras.layers import Layer
from tensorflow.python.keras import backend as K

try:  # tf v1.9 moves conv_utils from _impl to keras.utils
    from tensorflow.python.keras.utils import conv_utils
except ImportError:
    from tensorflow.python.keras._impl.keras.utils import conv_utils

from deepcell.utils import retinanet_anchor_utils


class Anchors(Layer):
    """Keras layer for generating achors for a given shape.

    Args:
        size: The base size of the anchors to generate.
        stride: The stride of the anchors to generate.
        ratios: The ratios of the anchors to generate,
            defaults to AnchorParameters.default.ratios.
        scales: The scales of the anchors to generate,
            defaults to AnchorParameters.default.scales.
        data_format: A string,
            one of `channels_last` (default) or `channels_first`.
            The ordering of the dimensions in the inputs.
            `channels_last` corresponds to inputs with shape
            `(batch, height, width, channels)` while `channels_first`
            corresponds to inputs with shape
            `(batch, channels, height, width)`.
            It defaults to the `image_data_format` value found in your
            Keras config file at `~/.keras/keras.json`.
            If you never set it, then it will be "channels_last".
    """

    def __init__(self,
                 size,
                 stride,
                 ratios=None,
                 scales=None,
                 data_format=None,
                 *args,
                 **kwargs):
        super(Anchors, self).__init__(*args, **kwargs)
        self.data_format = conv_utils.normalize_data_format(data_format)
        self.size = size
        self.stride = stride
        self.ratios = ratios
        self.scales = scales

        if ratios is None:
            self.ratios = retinanet_anchor_utils.AnchorParameters.default.ratios
        elif isinstance(ratios, list):
            self.ratios = np.array(ratios)
        if scales is None:
            self.scales = retinanet_anchor_utils.AnchorParameters.default.scales
        elif isinstance(scales, list):
            self.scales = np.array(scales)

        self.num_anchors = len(self.ratios) * len(self.scales)
        self.anchors = K.variable(retinanet_anchor_utils.generate_anchors(
            base_size=size, ratios=ratios, scales=scales))

        super(Anchors, self).__init__(*args, **kwargs)

    def call(self, inputs, **kwargs):
        features_shape = K.shape(inputs)

        # generate proposals from bbox deltas and shifted anchors
        row_axis = 2 if self.data_format == 'channels_first' else 1
        anchors = retinanet_anchor_utils.shift(
            features_shape[row_axis:row_axis + 2], self.stride, self.anchors)

        anchors = tf.tile(K.expand_dims(anchors, axis=0),
                          (features_shape[0], 1, 1))

        return anchors

    def compute_output_shape(self, input_shape):
        input_shape = tensor_shape.TensorShape(input_shape).as_list()
        if None not in input_shape[1:]:
            if self.data_format == 'channels_first':
                total = np.prod(input_shape[2:4]) * self.num_anchors
            else:
                total = np.prod(input_shape[1:3]) * self.num_anchors

            return tensor_shape.TensorShape((input_shape[0], total, 4))
        else:
            return tensor_shape.TensorShape((input_shape[0], None, 4))

    def get_config(self):
        config = {
            'size': self.size,
            'stride': self.stride,
            'ratios': self.ratios.tolist(),
            'scales': self.scales.tolist(),
            'data_format': self.data_format,
        }
        base_config = super(Anchors, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))


class RegressBoxes(Layer):
    """Keras layer for applying regression values to boxes."""

    def __init__(self, mean=None, std=None, data_format=None, *args, **kwargs):
        """Initializer for the RegressBoxes layer.

        Args:
            mean: The mean value of the regression values
                which was used for normalization.
            std:  The standard value of the regression values
                which was used for normalization.
            data_format: A string,
                one of `channels_last` (default) or `channels_first`.
                The ordering of the dimensions in the inputs.
                `channels_last` corresponds to inputs with shape
                `(batch, height, width, channels)` while `channels_first`
                corresponds to inputs with shape
                `(batch, channels, height, width)`.
                It defaults to the `image_data_format` value found in your
                Keras config file at `~/.keras/keras.json`.
                If you never set it, then it will be "channels_last".
        """
        super(RegressBoxes, self).__init__(*args, **kwargs)
        self.data_format = conv_utils.normalize_data_format(data_format)

        if mean is None:
            mean = np.array([0, 0, 0, 0])
        if std is None:
            std = np.array([0.2, 0.2, 0.2, 0.2])

        if isinstance(mean, (list, tuple)):
            mean = np.array(mean)
        elif not isinstance(mean, np.ndarray):
            raise ValueError('Expected mean to be a np.ndarray, list or tuple.'
                             ' Received: {}'.format(type(mean)))

        if isinstance(std, (list, tuple)):
            std = np.array(std)
        elif not isinstance(std, np.ndarray):
            raise ValueError('Expected std to be a np.ndarray, list or tuple. '
                             'Received: {}'.format(type(std)))

        self.mean = mean
        self.std = std

    def call(self, inputs, **kwargs):
        anchors, regression = inputs
        return retinanet_anchor_utils.bbox_transform_inv(
            anchors, regression, mean=self.mean, std=self.std)

    def compute_output_shape(self, input_shape):
        # input_shape = tensor_shape.TensorShape(input_shape).as_list()
        return tensor_shape.TensorShape(input_shape[0])

    def get_config(self):
        config = {
            'mean': self.mean.tolist(),
            'std': self.std.tolist(),
            'data_format': self.data_format
        }
        base_config = super(RegressBoxes, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))


class ClipBoxes(Layer):
    """Keras layer to clip box values to lie inside a given shape."""

    def __init__(self, data_format=None, **kwargs):
        super(ClipBoxes, self).__init__(**kwargs)
        self.data_format = conv_utils.normalize_data_format(data_format)

    def call(self, inputs, **kwargs):
        image, boxes = inputs
        shape = K.cast(K.shape(image), K.floatx())
        if self.data_format == 'channels_first':
            height = shape[2]
            width = shape[3]
        else:
            height = shape[1]
            width = shape[2]
        x1 = tf.clip_by_value(boxes[:, :, 0], 0, width)
        y1 = tf.clip_by_value(boxes[:, :, 1], 0, height)
        x2 = tf.clip_by_value(boxes[:, :, 2], 0, width)
        y2 = tf.clip_by_value(boxes[:, :, 3], 0, height)

        return K.stack([x1, y1, x2, y2], axis=2)

    def compute_output_shape(self, input_shape):
        return tensor_shape.TensorShape(input_shape[1]).as_list()
        # return input_shape[1]

    def get_config(self):
        config = {'data_format': self.data_format}
        base_config = super(ClipBoxes, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))


class ConcatenateBoxes(Layer):
    def call(self, inputs, **kwargs):
        boxes, other = inputs
        boxes_shape = K.shape(boxes)
        other_shape = K.shape(other)
        other = K.reshape(other, (boxes_shape[0], boxes_shape[1], -1))
        return K.concatenate([boxes, other], axis=2)

    def compute_output_shape(self, input_shape):
        boxes_shape, other_shape = input_shape
        output_shape = tuple(list(boxes_shape[:2]) +
                             [K.prod([s for s in other_shape[2:]]) + 4])
        return tensor_shape.TensorShape(output_shape)

class ConcatenateBoxesMasks(Layer):
    def call(self, inputs, **kwargs):
        detections, masks = inputs
        boxes = detections[:, :, :4]

        boxes_shape = K.shape(boxes)
        masks_shape = K.shape(masks)
        masks = K.reshape(masks, (masks_shape[0], boxes_shape[1], -1))

        return K.concatenate([boxes, masks], axis=2)

    def compute_output_shape(self, input_shape):
        detections_shape, masks_shape = input_shape
        output_shape = masks_shape[:2] + (masks_shape[2] * masks_shape[3],)
        return tensor_shape.TensorShape(output_shape)


class RoiAlign(Layer):
    def __init__(self, top_k=256, crop_size=(14, 14), **kwargs):
        self.crop_size = crop_size
        self.top_k = top_k
        super(RoiAlign, self).__init__(**kwargs)

    def log2(self, x):
        return K.log(x) / K.log(K.cast(2.0, x.dtype))

    def map_to_level(self, boxes, canonical_size=224, canonical_level=1, min_level=0, max_level=4):
        x1 = boxes[:, :, 0]
        y1 = boxes[:, :, 1]
        x2 = boxes[:, :, 2]
        y2 = boxes[:, :, 3]

        w = x2 - x1
        h = y2 - y1

        size = K.sqrt(w * h)

        levels = tf.floor(canonical_level + self.log2(size / canonical_size + K.epsilon()))
        levels = K.clip(levels, min_level, max_level)

        return levels

    def call(self, inputs, **kwargs):
        image_shape = K.cast(inputs[0], K.floatx())
        boxes = K.stop_gradient(inputs[1])
        classification = K.stop_gradient(inputs[2])
        fpn = [K.stop_gradient(i) for i in inputs[3:]]

        # compute best scores for each detection
        scores = K.max(classification, axis=-1)
        print(boxes.get_shape(), classification.get_shape())

        # select the top k for mask ROI computation
        k=K.minimum(self.top_k, K.shape(boxes)[1])
        _, indices     = tf.nn.top_k(scores, k=self.top_k, sorted=False)
        boxes          = tf.batch_gather(boxes, indices)
        classification = tf.batch_gather(classification, indices)
        
        print(indices.get_shape(), boxes.get_shape(), classification.get_shape())

        # compute from which level to get features from
        target_levels = self.map_to_level(boxes)

        # process each pyramid independently
        rois = []
        ordered_boxes = []
        ordered_classification = []
        for i in range(len(fpn)):
            # select the boxes and classification from this pyramid level
            level_indices = tf.where(K.equal(target_levels, i))
            box_indices = tf.cast(level_indices[:,0], tf.int32)

            level_boxes = tf.gather_nd(boxes, level_indices)
            level_classification = tf.gather_nd(classification, level_indices)

            ordered_boxes.append(level_boxes)
            ordered_classification.append(level_classification)

            fpn_shape = K.cast(K.shape(fpn[i]), dtype=K.floatx())

            # convert to expected format for crop_and_resize
            x1 = level_boxes[:, 0]
            y1 = level_boxes[:, 1]
            x2 = level_boxes[:, 2]
            y2 = level_boxes[:, 3]
            level_boxes_reformat = K.stack([
                (y1 / image_shape[1] * fpn_shape[0]) / (fpn_shape[0] - 1),
                (x1 / image_shape[2] * fpn_shape[1]) / (fpn_shape[1] - 1),
                (y2 / image_shape[1] * fpn_shape[0] - 1) / (fpn_shape[0] - 1),
                (x2 / image_shape[2] * fpn_shape[1] - 1) / (fpn_shape[1] - 1),
            ], axis=1)

            # append the rois to the list of rois
            rois.append(tf.image.crop_and_resize(
                fpn[i],
                level_boxes_reformat,
                box_indices,
                self.crop_size
            ))

        # reassemble the boxes in a different order
        boxes = K.concatenate(ordered_boxes, axis=0)
        classification = K.concatenate(ordered_classification, axis=0)

        # concatenate rois to one blob
        rois = K.concatenate(rois, axis=0)

        # Re-add the batch dimension
        shape_rois = K.concatenate([
            K.shape(indices)[:2],
            K.shape(rois)[1:]
        ], axis=0)

        shape_boxes = K.concatenate([
            K.shape(indices)[:2],
            K.shape(boxes)[1:]
        ], axis=0)

        shape_classification = K.concatenate([
            K.shape(indices)[:2],
            K.shape(classification)[1:]
        ], axis=0)

        rois = K.reshape(rois, shape_rois)
        boxes = K.reshape(boxes, shape_boxes)
        classification = K.reshape(classification, shape_classification)
        return [boxes, classification, rois]

    def compute_output_shape(self, input_shape):
        # input_shape = [tensor_shape.TensorShape(i).as_list() for i in input_shape]
        output_shape = [
            input_shape[1][0],
            None,
            self.crop_size[0],
            self.crop_size[1],
            input_shape[3][-1]
        ]
        return tensor_shape.TensorShape(output_shape)

    def get_config(self):
        config = {'crop_size': self.crop_size, 'top_k': self.top_k}
        base_config = super(RoiAlign, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))


class Shape(Layer):
    def call(self, inputs, **kwargs):
        return K.shape(inputs)

    def compute_output_shape(self, input_shape):
        return tensor_shape.TensorShape((len(input_shape),))


class Cast(Layer):
    def __init__(self, dtype=None, *args, **kwargs):
        if dtype is None:
            dtype = K.floatx()
        self.dtype = dtype
        super(Cast, self).__init__(*args, **kwargs)

    def call(self, inputs, **kwargs):
        outputs = K.cast(inputs, self.dtype)
        return outputs