# Copyright 2016-2019 The Van Valen Lab at the California Institute of
# Technology (Caltech), with support from the Paul Allen Family Foundation,
# Google, & National Institutes of Health (NIH) under Grant U24CA224309-01.
# All rights reserved.
#
# Licensed under a modified Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.github.com/vanvalenlab/deepcell-tf/LICENSE
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
"""Code inspired by Piotr Dollar's panoptic segmentation paper"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from tensorflow.python.keras import backend as K
from tensorflow.python.keras.models import Model
from tensorflow.python.keras.layers import Conv2D
from tensorflow.python.keras.layers import MaxPool2D
from tensorflow.python.keras.layers import Softmax
from tensorflow.python.keras.layers import Input, Concatenate, Add
from tensorflow.python.keras.layers import Permute, Reshape
from tensorflow.python.keras.layers import Activation
from tensorflow.python.keras.layers import UpSampling2D
from tensorflow.python.keras.layers import BatchNormalization
from tensorflow.python.keras.layers import Activation
from tensorflow.python.keras import applications

from deepcell.layers import UpsampleLike
from deepcell.layers import TensorProduct, ImageNormalization2D

import re

def dc_block(x,n_filters):

    # conv set 1
    x = Conv2D(n_filters,(3,3),strides=(1,1),padding='same',data_format='channels_last')(x)
    x = BatchNormalization(axis=-1)(x)
    x = Activation('relu')(x)
    # conv set 2
    x = Conv2D(n_filters,(3,3),strides=(1,1),padding='same',data_format='channels_last')(x)
    x = BatchNormalization(axis=-1)(x)
    x = Activation('relu')(x)
    # Final max pooling stage
    x = MaxPool2D(pool_size=(2,2),data_format='channels_last')(x)

    return (x)

def dc_model(input_tensor=None, weights=None, include_top=False, pooling=None, n_filters=32, n_dense=128, n_classes=3):

    # Build out backbone
    c1 = dc_block(input_tensor,n_filters) # 1/2 64x64
    c2 = dc_block(c1,n_filters) # 1/4 32x32
    c3 = dc_block(c2,n_filters) # 1/8 16x16
    c4 = dc_block(c3,n_filters) # 1/16 8x8
    c5 = dc_block(c4,n_filters) # 1/32 4x4
    
    backbone_features = [c1, c2, c3, c4, c5]
    backbone_names = ['C1', 'C2', 'C3', 'C4', 'C5']

    return backbone_names, backbone_features

def get_pyramid_layer_outputs(backbone, inputs, **kwargs):
    _backbone = str(backbone).lower()

    resnet_backbones = {'resnet50'}
    deepcell_backbones = {'deepcell'}

    if _backbone in resnet_backbones:
        model = applications.ResNet50(**kwargs)
        layer_names = ['res2c_branch2c', 'res3d_branch2c', 'res4f_branch2c', 'res5c_branch2c']
        layer_outputs = [model.get_layer(name).output for name in layer_names]
        model = Model(inputs=inputs, outputs=layer_outputs, name=model.name)
        backbone_names = ['C2', 'C3', 'C4', 'C5']
        return backbone_names, model.outputs

    if _backbone in deepcell_backbones:
        return dc_model(**kwargs)

    else:
        backbones = list(resnet_backbones) + list(deepcell_backbones)
        raise ValueError('Invalid value for `backbone`. Must be one of: %s' %
                         ', '.join(backbones))

def create_pyramid_level(backbone_input, upsamplelike_input=None, addition_input=None, level=5, feature_size=256):

    reduced_name = 'C' + str(level) + '_reduced'
    upsample_name = 'P' + str(level) + '_upsampled'
    addition_name = 'P' + str(level) + '_merged'
    final_name = 'P' + str(level)

    # Apply 1x1 conv to backbone layer
    pyramid = Conv2D(feature_size, (1,1), strides=(1,1), padding='same', name=reduced_name)(backbone_input)

    # Upsample pyramid input
    if upsamplelike_input is not None:
        pyramid_upsample = UpsampleLike(name=upsample_name)([pyramid, upsamplelike_input])
    else:
        pyramid_upsample = None

    # Add and then 3x3 conv
    if addition_input is not None:
        pyramid = Add(name=addition_name)([pyramid, addition_input])

    pyramid_final = Conv2D(feature_size, (3,3), strides=(1,1), padding='same', name=final_name)(pyramid)

    return pyramid_final, pyramid_upsample

def __create_pyramid_features(backbone_names, backbone_features, feature_size=256):
    """Creates the FPN layers on top of the backbone features.

    Args:
        backbone_names: A list of all the backbone names in ascending order,
            e.g. [C0, C1, C2, C3, ...]
        backbone_features: A list of all the backbone features
        feature_size: The feature size to use for the resulting feature levels.

    Returns:
        A list of feature levels [P3, P4, P5, P6, P7].
    """

    pyramid_names = []
    pyramid_finals = []
    pyramid_upsamples = []

    # Reverse lists
    backbone_names.reverse()
    backbone_features.reverse()

    for i in range(len(backbone_names)):
        
        N = backbone_names[i]
        level = int(re.findall(r'\d+', N)[0])
        p_name = 'P' + str(level)
        pyramid_names.append(p_name)

        backbone_input = backbone_features[i]

        # Don't add for the bottom of the pyramid
        if i == 0:
            upsamplelike_input = backbone_features[i+1]
            addition_input = None
        
        # Don't upsample for the top of the pyramid
        elif i == len(backbone_names)-1:
            upsamplelike_input = None
            addition_input = pyramid_upsamples[-1]

        # Otherwise, add and upsample
        else:
            upsamplelike_input = backbone_features[i+1]
            addition_input = pyramid_upsamples[-1]

        pf, pu = create_pyramid_level(backbone_input, upsamplelike_input=upsamplelike_input,
                        addition_input=addition_input, level=level)
        pyramid_finals.append(pf)
        pyramid_upsamples.append(pu)

    # Add the final two pyramid layers
    # "Second to last pyramid layer is obtained via a 3x3 stride-2 conv on the coarsest backbone"
    N = backbone_names[0]
    F = backbone_features[0]
    level = int(re.findall(r'\d+', N)[0]) + 1
    P_minus_2_name = 'P' + str(level)
    P_minus_2 = Conv2D(feature_size, kernel_size=3, strides=2, padding='same', name=P_minus_2_name)(F)
    pyramid_names.insert(0, P_minus_2_name)
    pyramid_finals.insert(0, P_minus_2)

    # "Last pyramid layer is computed by applying ReLU followed by a 3x3 stride-2 conv on second to last layer"
    level = int(re.findall(r'\d+', N)[0]) + 2
    P_minus_1_name = 'P' + str(level)
    P_minus_1 = Activation('relu', name=N+'_relu')(P_minus_2)
    P_minus_1= Conv2D(feature_size, kernel_size=3, strides=2, padding='same', name=P_minus_1_name)(P_minus_1)
    pyramid_names.insert(0, P_minus_1_name)
    pyramid_finals.insert(0, P_minus_1)

    pyramid_names.reverse()
    pyramid_finals.reverse()

    # Reverse lists
    backbone_names.reverse()
    backbone_features.reverse()

    return pyramid_names, pyramid_finals


def semantic_upsample(x, n_upsample, n_filters=256, target=None):
    """
    Performs iterative rounds of 2x upsampling and 
    convolutions with a 3x3 filter to remove aliasing effects

    Args:
        x: The input tensor to be upsampled
        n_upsample: The number of 2x upsamplings
        n_filters: The number of filters for the 3x3 convolution
        target: A tensor with the target shape. If included, then
            the final upsampling layer will reshape to the target
            tensor's size

    Returns:
        The upsampled tensor
    """
    for i in range(n_upsample):
        x = Conv2D(n_filters, (3,3), strides=(1,1), padding='same', data_format='channels_last')(x)

        if i == n_upsample-1 and target is not None:
            x = UpsampleLike()([x, target])
        else:
            x = UpSampling2D(size=(2,2))(x)

    if n_upsample == 0:
        x = Conv2D(n_filters, (3,3), strides=(1,1), padding='same', data_format='channels_last')(x)

        if target is not None:
            x =UpsampleLike()([x, target])

    return x

def semantic_prediction(semantic_names, semantic_features, target_level=0, input_target=None, 
        n_filters=256, n_dense=256, n_classes=3):
    """
    Creates the prediction head from a list of semantic features

    Args:
        semantic_names: A list of the names of the semantic feature layers
        semantic_features: A list of semantic features 

        NOTE: The semantic_names and semantic features should be in decreasing order
        e.g. [Q6, Q5, Q4, ...]
        
        target_level: The level we need to reach - performs 2x upsampling
            until we're at the target level
        input_target: Tensor with the input image. 
        n_filters: The number of filters for convolution layers
        n_features: The number of classes to be predicted

    Returns:
        The softmax prediction for the semantic segmentation head
    """
    if K.image_data_format() == 'channels_first':
        channel_axis = 1
    else:
        channel_axis = -1

    # Add all the semantic layers
    semantic_sum = semantic_features[0]
    for semantic_feature in semantic_features[1:]:
        semantic_sum = Add()([semantic_sum, semantic_feature])

    #Final upsampling
    min_level = int(re.findall(r'\d+', semantic_names[-1])[0])
    n_upsample = min_level-target_level
    x = semantic_upsample(semantic_sum, n_upsample, target=input_target)

    # First tensor product
    x = TensorProduct(n_dense)(x)
    x = BatchNormalization(axis=-1)(x)
    x = Activation('relu')(x)

    #Apply tensor product and softmax layer
    x = TensorProduct(n_classes)(x)
    x = Softmax(axis=channel_axis)(x)

    return x

def __create_semantic_head(pyramid_names, pyramid_features, input_target=None, target_level=2, n_classes=3, n_filters=128):
    """
    Creates a semantic head from a feature pyramid network

    Args:
        pyramid_names: A list of the names of the pyramid levels, e.g
            ['P3', 'P4', 'P5', 'P6'] in increasing order
        pyramid_features: A list with the pyramid level tensors in the
            same order as pyramid names
        input_target: Tensor with the input image. 
        target_level: The level we'll seek to up sample to. Level 1 = 1/2^1 size,
            Level 2 = 1/2^2 size, Level 3 = 1/2^3 size, etc.
        n_filters: The number of filters for the convolutional layer

    Returns:
        The semantic segmentation head
    """

    # Reverse pyramid names and features
    pyramid_names.reverse()
    pyramid_features.reverse()

    semantic_features = []
    semantic_names = []
    # for P in pyramid_features:
    #     print(P.get_shape())

    for N, P in zip(pyramid_names, pyramid_features):
        # Get level and determine how much to upsample
        level = int(re.findall(r'\d+', N)[0])

        n_upsample = level-target_level
        target = semantic_features[-1] if len(semantic_features) > 0 else None

        # Use semantic upsample to get semantic map
        semantic_features.append(semantic_upsample(P, n_upsample, n_filters=n_filters, target=target))
        semantic_names.append('Q' + str(level))

    # Combine all of the semantic features
    x = semantic_prediction(semantic_names, semantic_features, n_classes=n_classes, input_target=input_target)

    return x

def FPNet(backbone, 
          input_shape,
          norm_method='whole_image',
          weights=None,
          pooling=None,
          required_channels=3,
          n_classes=3,
          name='fpnet',
          **kwargs):
    """
    Creates a Feature Pyramid Network with a semantic segmentation head

    Args:
        backbone: A name of a supported backbone
        input_shape: Shape of the input image
        norm_method: Normalization method
        weights: one of `None` (random initialization),
            'imagenet' (pre-training on ImageNet),
            or the path to the weights file to be loaded.
        pooling: optional pooling mode for feature extraction
            when `include_top` is `False`.
            - `None` means that the output of the model will be
                the 4D tensor output of the
                last convolutional layer.
            - `avg` means that global average pooling
                will be applied to the output of the
                last convolutional layer, and thus
                the output of the model will be a 2D tensor.
            - `max` means that global max pooling will
                be applied.
        required_channels: integer, the required number of channels of the
            backbone.  3 is the default for all current backbones.

    Returns:
        Model with a feature pyramid network with a semantic segmentation
        head as the output

    """

    inputs = Input(shape=input_shape)
    # force the channel size for backbone input to be `required_channels`
    norm = ImageNormalization2D(norm_method=norm_method)(inputs)
    fixed_inputs = TensorProduct(required_channels)(norm)
    model_kwargs = {
        'include_top': False,
        'input_tensor': fixed_inputs,
        'weights': weights,
        'pooling': pooling
    }

    # Get backbone outputs
    backbone_names, backbone_features = get_pyramid_layer_outputs(backbone, inputs, **model_kwargs)

    # Construct feature pyramid network
    fpn_names, fpn_features = __create_pyramid_features(backbone_names, backbone_features)

    levels = [int(re.findall(r'\d+', N)[0]) for N in fpn_names]
    target_level = min(levels)
    # x = fpn_features[0]
    # x = UpsampleLike()([x, inputs])
    # x = TensorProduct(n_classes)(x)
    # x = Softmax(axis=-1)(x)

    # Construct semantic head
    fpn_names = fpn_names[0:-1]
    fpn_features = fpn_features[0:-1]

    x = __create_semantic_head(fpn_names, fpn_features, n_classes=n_classes, input_target=inputs, target_level=target_level)

    # Return model
    return Model(inputs=inputs, outputs=x, name=name)

        

