# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import mlflow
from mlflow.exceptions import MlflowException
import os
from pathlib import Path
from contextlib import contextmanager
from .exp import MLflowExperiment
from .recorder import Recorder, MLflowRecorder
from ..log import get_module_logger

logger = get_module_logger("workflow", "INFO")


class ExpManager:
    """
    This is the `ExpManager` class for managing experiments. The API is designed similar to mlflow.
    (The link: https://mlflow.org/docs/latest/python_api/mlflow.html)
    """

    def __init__(self, uri, default_exp_name):
        self.uri = uri
        self.default_exp_name = default_exp_name
        self.active_experiment = None  # only one experiment can running each time

    def start_exp(self, experiment_name=None, recorder_name=None, uri=None, **kwargs):
        """
        Start an experiment. This method includes first get_or_create an experiment, and then
        set it to be running.

        Parameters
        ----------
        experiment_name : str
            name of the active experiment.
        recorder_name : str
            name of the recorder to be started.
        uri : str
            the current tracking URI.

        Returns
        -------
        An active experiment.
        """
        raise NotImplementedError(f"Please implement the `start_exp` method.")

    def end_exp(self, recorder_status: str = Recorder.STATUS_S, **kwargs):
        """
        End an running experiment.

        Parameters
        ----------
        experiment_name : str
            name of the active experiment.
        recorder_status : str
            the status of the active recorder of the experiment.
        """
        raise NotImplementedError(f"Please implement the `end_exp` method.")

    def create_exp(self, experiment_name=None):
        """
        Create an experiment.

        Parameters
        ----------
        experiment_name : str
            the experiment name, which must be unique.

        Returns
        -------
        An experiment object.
        """
        raise NotImplementedError(f"Please implement the `create_exp` method.")

    def search_records(self, experiment_ids=None, **kwargs):
        """
        Get a pandas DataFrame of records that fit the search criteria of the experiment.
        Inputs are the search critera user want to apply.

        Returns
        -------
        A pandas.DataFrame of records, where each metric, parameter, and tag
        are expanded into their own columns named metrics.*, params.*, and tags.*
        respectively. For records that don't have a particular metric, parameter, or tag, their
        value will be (NumPy) Nan, None, or None respectively.
        """
        raise NotImplementedError(f"Please implement the `search_records` method.")

    def get_exp(self, experiment_id=None, experiment_name=None, create: bool = True):
        """
        Retrieve an experiment. This method includes getting an active experiment, and get_or_create a specific experiment.
        The returned experiment will be running.

        When user specify experiment id and name, the method will try to return the specific experiment.
        When user does not provide recorder id or name, the method will try to return the current active experiment.
        The `create` argument determines whether the method will automatically create a new experiment according
        to user's specification if the experiment hasn't been created before

        If `create` is True:
            If R's running:
                1) no id or name specified, return the active experiment.
                2) if id or name is specified, return the specified experiment. If no such exp found,
                create a new experiment with given id or name, and the experiment is set to be running.
            If R's not running:
                1) no id or name specified, create a default experiment.
                2) if id or name is specified, return the specified experiment. If no such exp found,
                create a new experiment with given id or name, and the experiment is set to be running.
        Else If `create` is False:
            If R's running:
                1) no id or name specified, return the active experiment.
                2) if id or name is specified, return the specified experiment. If no such exp found,
                raise Error.
            If R's not running:
                1) no id or name specified. If the default experiment exists, return it, otherwise, raise Error.
                2) if id or name is specified, return the specified experiment. If no such exp found,
                raise Error.

        Parameters
        ----------
        experiment_id : str
            id of the experiment to return.
        experiment_name : str
            name of the experiment to return.
        create : boolean
            create the experiment it if hasn't been created before.

        Returns
        -------
        An experiment object.
        """
        raise NotImplementedError(f"Please implement the `get_exp` method.")

    def delete_exp(self, experiment_id=None, experiment_name=None):
        """
        Delete an experiment.

        Parameters
        ----------
        experiment_id  : str
            the experiment id.
        experiment_name  : str
            the experiment name.
        """
        raise NotImplementedError(f"Please implement the `delete_exp` method.")

    def get_uri(self):
        """
        Get the default tracking URI or current URI.

        Returns
        -------
        The tracking URI string.
        """
        return self.uri

    def list_experiments(self):
        """
        List all the existing experiments.

        Returns
        -------
        A dictionary (name -> experiment) of experiments information that being stored.
        """
        raise NotImplementedError(f"Please implement the `list_experiments` method.")


class MLflowExpManager(ExpManager):
    """
    Use mlflow to implement ExpManager.
    """

    def __init__(self, uri, default_exp_name):
        super(MLflowExpManager, self).__init__(uri, default_exp_name)
        self.client = mlflow.tracking.MlflowClient(tracking_uri=self.uri)

    def start_exp(self, experiment_name=None, recorder_name=None, uri=None):
        # create experiment
        experiment, _ = self._get_or_create_exp(experiment_name=experiment_name)
        # set up active experiment
        self.active_experiment = experiment
        # start the experiment
        self.active_experiment.start(recorder_name)
        # set the tracking uri
        if uri is None:
            logger.info(
                "No tracking URI is provided. The default tracking URI is set as `mlruns` under the working directory."
            )
        else:
            self.uri = uri
        mlflow.set_tracking_uri(self.uri)

        return self.active_experiment

    def end_exp(self, recorder_status: str = Recorder.STATUS_S):
        if self.active_experiment is not None:
            self.active_experiment.end(recorder_status)
            self.active_experiment = None

    def create_exp(self, experiment_name=None):
        # init experiment
        experiment_id = self.client.create_experiment(experiment_name)
        experiment = MLflowExperiment(experiment_id, experiment_name, self.uri)
        experiment._default_name = self.default_exp_name

        return experiment

    def get_exp(self, experiment_id=None, experiment_name=None, create=True):
        # special case of getting experiment
        if experiment_id is None and experiment_name is None:
            if self.active_experiment is not None:
                return self.active_experiment
        if create:
            exp, is_new = self._get_or_create_exp(experiment_id=experiment_id, experiment_name=experiment_name)
        else:
            exp, is_new = self._get_exp(experiment_id=experiment_id, experiment_name=experiment_name), False
        if is_new:
            self.active_experiment = exp
            # start the recorder
            self.active_experiment.start()
        return exp

    def _get_or_create_exp(self, experiment_id=None, experiment_name=None) -> (object, bool):
        """
        Method for getting or creating an experiment. It will try to first get a valid experiment, if exception occurs, it will
        automatically create a new experiment based on the given id and name.
        """
        try:
            return self._get_exp(experiment_id=experiment_id, experiment_name=experiment_name), False
        except ValueError:
            if experiment_name is None:
                experiment = self.default_exp_name
            logger.info(f"No valid experiment found. Create a new experiment with name {experiment_name}.")
            return self.create_exp(experiment_name), True

    def _get_exp(self, experiment_id=None, experiment_name=None):
        """
        Method for getting or creating an experiment. It will try to first get a valid experiment, if exception occurs, it will
        raise errors.
        """
        assert (
            experiment_id is not None or experiment_name is not None
        ), "Please input at least one of experiment/recorder id or name before retrieving experiment/recorder."
        if experiment_id is not None:
            try:
                exp = self.client.get_experiment(experiment_id)
                if exp.lifecycle_stage.upper() == "DELETED":
                    raise MlflowException("No valid experiment has been found.")
                experiment = MLflowExperiment(exp.experiment_id, exp.name, self.uri)
                return experiment
            except MlflowException as e:
                raise ValueError(
                    "No valid experiment has been found, please make sure the input experiment id is correct."
                )
        elif experiment_name is not None:
            try:
                exp = self.client.get_experiment_by_name(experiment_name)
                if exp is None or exp.lifecycle_stage.upper() == "DELETED":
                    raise MlflowException("No valid experiment has been found.")
                experiment = MLflowExperiment(exp.experiment_id, experiment_name, self.uri)
                return experiment
            except MlflowException as e:
                raise ValueError(
                    "No valid experiment has been found, please make sure the input experiment name is correct."
                )

    def search_records(self, experiment_ids, **kwargs):
        filter_string = "" if kwargs.get("filter_string") is None else kwargs.get("filter_string")
        run_view_type = 1 if kwargs.get("run_view_type") is None else kwargs.get("run_view_type")
        max_results = 100000 if kwargs.get("max_results") is None else kwargs.get("max_results")
        order_by = kwargs.get("order_by")
        return self.client.search_runs(experiment_ids, filter_string, run_view_type, max_results, order_by)

    def delete_exp(self, experiment_id=None, experiment_name=None):
        assert (
            experiment_id is not None or experiment_name is not None
        ), "Please input a valid experiment id or name before deleting."
        try:
            if experiment_id is not None:
                self.client.delete_experiment(experiment_id)
            else:
                experiment = self.client.get_experiment_by_name(experiment_name)
                if experiment is None:
                    raise MlflowException("No valid experiment has been found.")
                self.client.delete_experiment(experiment.experiment_id)
        except MlflowException as e:
            raise Exception(
                f"Error: {e}. Something went wrong when deleting experiment. Please check if the name/id of the experiment is correct."
            )

    def list_experiments(self):
        # retrieve all the existing experiments
        exps = self.client.list_experiments(view_type=1)
        experiments = dict()
        for exp in exps:
            experiment = MLflowExperiment(exp.experiment_id, exp.name, self.uri)
            experiments[ename] = experiment

        return experiments