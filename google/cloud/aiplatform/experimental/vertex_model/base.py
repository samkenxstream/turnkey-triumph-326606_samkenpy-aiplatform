# -*- coding: utf-8 -*-

# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import abc
import functools
import inspect
import logging
import sys
import threading
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Iterable,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
)

import proto

from google.api_core import operation
from google.auth import credentials as auth_credentials
from google.cloud.aiplatform import initializer
from google.cloud.aiplatform import utils
from google.cloud.aiplatform.compat.types import encryption_spec as gca_encryption_spec
from google.cloud import aiplatform
                         
from torch.utils.data import Dataset, Dataloader
from google.cloud.aiplatform.experimental.vertex_model import serializers
from google.cloud.aiplatform.experimental.vertex_model import source

try:
    import pandas as pd
except ImportError:
    raise ImportError("Pandas is not installed. Please install pandas to use VertexModel")

try:
    import torch 
except ImportError:
    raise ImportError("PyTorch is not installed. Please install torch to use VertexModel")

# Wrapper function to handle cloud training extension of user code
def vertex_fit_function_wrapper(method):

    @functools.wraps(method)
    def f(*args, **kwargs):
        if method.__self__.training_mode == 'local':
            return method(*args, **kwargs)
        
        obj = method.__self__
        cls_name = obj.__class__.__name__

        training_source = _make_class_source(obj)

        source = _make_source(
            cls_source=training_source,
            cls_name=cls_name,
            instance_method=method.__name__)
            
        with tempfile.TemporaryDirectory() as tmpdirname:
            script_path = pathlib.Path(tmpdirname) / "training_script.py"

            with open(script_path, 'w') as f:
                f.write(source)
        
            bound_args = inspect.signature(method).bind(*args, **kwargs)
            dataset = bound_args.arguments.get('dataset')
              
            staging_bucket = aiplatform.initializer.global_config.staging_bucket
            if staging_bucket is None:
                raise RuntimeError(
                    "Staging bucket must be set to run training in cloud mode: `aiplatform.init(staging_bucket='gs://my/staging/bucket')`")


            obj._training_job = aiplatform.CustomTrainingJob(
                display_name='my_training_job',
                script_path=str(script_path),

                # programatically determine the dependency in the future
                requirements = ['pandas>=1.3'],

                # https://cloud.google.com/vertex-ai/docs/training/pre-built-containers
                container_uri='us-docker.pkg.dev/vertex-ai/training/pytorch-xla.1-7:latest')
            
            # In the custom training job, a MODEL directory will be provided as an env var
            # our code should serialize our MODEL to that directory

            obj._training_job.run(replica_count=1)

    return f


class VertexModel(metaclass=abc.ABCMeta):

    _data_serialization_mapping = {
        pd.DataFrame : (_deserialize_dataframe, _serialize_dataframe)
    }

    """ Parent class that users can extend to use the Vertex AI SDK """
    def __init__(self):
        # Default to local training on creation, at least for this prototype.
        self.training_mode = 'local'

        self.fit = vertex_function_wrapper(self.fit)

    @abc.abstractmethod
    def fit(self):
        """ Train model. """
        pass

    @abc.abstractmethod
    def predict(self):
        """ Make predictions on training data. """
        raise NotImplementedError

    @abc.abstractmethod
    def batch_predict(self):
        """ Make predictions on training data. """
        raise NotImplementedError

    @abc.abstractmethod
    def eval(self):
        """ Evaluate model. """
        raise NotImplementedError
