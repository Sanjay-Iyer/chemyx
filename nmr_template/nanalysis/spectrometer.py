import logging
import requests
import time
import warnings
import tqdm
import numpy as np
from typing import Union, List, Tuple
from json.decoder import JSONDecodeError
from .helpers import NMRVersion, TemporarilySet
from .helpers import elaborate_setting_error as _elaborate_setting_error

# peak integration methods
PEAK_INTEGRATION_METHODS = {
    0: 'manual',
    1: 'automatic',
}

# # modifyable experiment settings
# MODDABLE_EXPT_SETTINGS = {
#     'Apodization',
#     'Experiment',
#     'NumberOfPoints',
#     'NumberOfScans',
#     'PeakIntegrationMethod',
#     'PulseWidthInMicroseconds',
#     'ReceiverGain',
#     'ScanDelayInSeconds',
#     'Solvent',
#     'SolventGroup',
#     'SpectralCentreInPpm',
#     'SpectralWidthInPpm',
#     'ZeroFillingFactor',
# }
# todo ask if there is a way to reimplement checks on this side

# get module-level logger
logger = logging.getLogger(__name__)


class BadResponseCode(Exception):
    def __init__(self,
                 api_cmd: str,
                 json_object: dict,
                 code_dictionary: dict,
                 result_code: int,
                 ):
        """
        An exception for bad response codes. The response code

        :param str api_cmd: The API command that was called
        :param dict json_object: the JSON object that was put to the API path
        :param dict code_dictionary: dictionary of result codes and meanings
        :param int result_code: returned result code
        """
        super(BadResponseCode, self).__init__(
            f'A bad response code ({result_code}) was returned from the spectrometer for putting "{json_object}" '
            f'in API call {api_cmd}: {code_dictionary[result_code]}.'
        )


DEFAULT_RESPONSE_CODES = {  # default response code meanings
    0: 'Success',
    1: 'Failure',
}


class NMR(object):
    PROTOCOL: str = 'http://'
    PORT: int = 5000
    _last_spectrometer_status: dict = None
    DEFAULT_LOGGING_LEVEL = logging.WARNING

    # whether to print progress by default (controls tqdm output for shim and experiment wait)
    PRINT_PROGRESS: bool = True

    def __init__(self,
                 address: str,
                 log_level: int = None,
                 ):
        """
        A python object that describes a Nanalysis NMReady spectrometer. Attributes and methods allow pythonic access to
        the NMR API.

        :param str address: network address to connect to the spectrometer on.
        :param log_level: logging level (int or valid logging level)
        """
        self._address = None
        self.logger = logger.getChild(address)
        if log_level is None:
            log_level = self.DEFAULT_LOGGING_LEVEL
        self.logger.level = log_level
        self.address = address

        # storage attributes for firmware and software version
        self._firmware_version: NMRVersion = None
        self._software_version: NMRVersion = None
        
    def __repr__(self):
        return f'{self.__class__.__name__}({self.address})'

    def __str__(self):
        return f'{self.__class__.__name__} at {self.address} performing {self.experiment} on {self.nucleus}'

    @property
    def address(self) -> str:
        """connection address"""
        return self._address

    @address.setter
    def address(self, value: str):
        old_address = self._address
        self._address = value
        if self.ping() is False:  # catch if ping fails
            try:
                response = requests.get(
                    f'{self.PROTOCOL}{self.address}:{self.PORT}/interfaces/iStatus/PingSpectrometer'
                )
                if response.reason != 'OK':
                    self._address = old_address
                    raise ConnectionError(
                        f'A connection could not be established with a Nanalysis spectrometer at {value}.'
                        f'reason: {response.reason}'
                    )
            except (ConnectionError, requests.exceptions.ConnectionError):
                self._address = old_address
                raise ConnectionError(
                    f'A connection could not be established with a Nanalysis spectrometer on {value}.'
                )
        # update logger name
        self.logger.name = self.__repr__()

    def ping(self):
        """Pings the spectrometer"""
        try:
            self._api_get('/interfaces/iStatus/PingSpectrometer')
        except (ConnectionError, requests.exceptions.ConnectionError):
            # any kind of connection error
            return False
        # do a check to see if RPC is on
        if self.rpc_enabled is False:
            logger.warning('The spectrometer is reachable but RPC is not enabled.')
        return True

    @property
    def rpc_enabled(self):
        """Whether Remote Procedure Calls (i.e. the API) are enabled"""
        return self._api_get('/interfaces/iStatus/RpcEnabled')['RpcEnabled']

    def _api_get(self, path: str) -> dict:
        """
        Queries the spectrometer with the exact string provided

        :param path: API path
        :return: response JSON object (as dictionary)
        :rtype: dict
        """
        if self.address is None:
            raise ConnectionError(f'An address is not set for the {self.__class__.__name__} instance')
        self.logger.debug(f'api get "{path}"')
        response = requests.get(
            f'{self.PROTOCOL}{self.address}:{self.PORT}/{path}'
        ).json()
        self.logger.debug(f'api get response "{response}"')
        return response

    def _api_put(self, path: str, request: dict) -> dict:
        """
        Sends the request to the spectrometer path. No error will be raised should a bad response code be encountered. 

        :param path: API path
        :param request: json-like request contents
        :return: response JSON
        :rtype: dict
        """
        try:
            self.logger.debug(f'api put "{request}" @ "{path}" ')
            response = requests.put(
                f'{self.PROTOCOL}{self.address}:{self.PORT}/{path}',
                json=request,
            )
            # separating json conversion allows more nuanced error catches
            response = response.json()
        except JSONDecodeError as error:
            if 'RPC Enabled: False' in str(response.content):
                err_str = (
                    'RPC is not enabled on the spectrometer, and values cannot be set using API. '
                    'Enable RPC in Setup > System > Remote'
                )
            elif response.reason == 'INTERNAL SERVER ERROR':
                err_str = (
                    'An internal server error was encountered. This has been known to happen if an attempt '
                    'was made to pass an invalid value to the API. '
                )
            else:
                err_str = f'An uncaught JSON decoding error was encountered: {error}'
            self.logger.error(err_str)
            raise ValueError(err_str)
        self.logger.debug(f'api put response "{response}"')
        return response

    def _api_put_and_parse(self,
                           path: str,
                           request: dict,
                           codes: dict = None,
                           ) -> bool:
        """
        Convenience wrapper which puts a request in the specified API path, then interprets the response
        in the context of a dictionary of response codes. A response code of 0 is interpreted as a success, while
        other response codes raise a BadResponseCode error.

        :param str path: API path
        :param dict request: JSON-like request contents
        :param dict codes: dictionary of response codes (int) and meanings (string)
        :raises BadResponseCode: If the response code is not 0
        :return: success
        :rtype: bool
        """
        if codes is None:
            codes = DEFAULT_RESPONSE_CODES
        response = self._api_put(
            path,
            request
        )
        if 'ResultCode' not in response:  # if a weird response was returned, warn
            warning = f'A response not containing a ResponseCode was returned for API put {request} in {path}: {response}'
            self.logger.warning(warning)
            warnings.warn(warning, stacklevel=2)
            return False
        elif response['ResultCode'] != 0:  # if a bad response code was returned, raise error
            if response['ResultCode'] not in codes:  # catch for undefined response codes
                codes = dict(codes)
                codes[response['ResultCode']] = 'Undefined result code for API call.'
            self.logger.error(f'Bad response code: {response["ResultCode"]}')
            raise BadResponseCode(
                path,
                request,
                codes,
                response['ResultCode'],
            )
        return True

    @property
    def standby(self) -> bool:
        """Whether instrument is in standby mode"""
        return self._api_get('/interfaces/iStatus/StandbyMode')['StandbyMode']

    @standby.setter
    def standby(self, value: bool):
        self.logger.info(f'Setting instrument standby status to {value}')
        self._api_put_and_parse(
            '/interfaces/iStatus/StandbyMode',
            {'StandbyMode': value}
        )

    def _update_constants(self):
        """Updates the attributes which remain constant on the spectrometer"""
        # todo expand this for all constants
        status = self.spectrometer_status
        self._software_version = NMRVersion(status['SoftwareVersion'])
        self._firmware_version = NMRVersion(status['FirmwareVersion'])

    @property
    def software_version(self) -> str:
        """software version for the instrument"""
        if self._software_version is None:
            self._update_constants()
        return str(self._software_version)

    @property
    def firmware_version(self) -> str:
        """firmware version for the instrument"""
        if self._firmware_version is None:
            self._update_constants()
        return str(self._firmware_version)

    def run_experiment(self,
                       wait: bool = False,
                       verbose: bool = True,
                       **kwargs
                       ) -> bool:
        """
        Runs an experiment with the current settings. Any additional settings may be changed using keyword arguments.

        :param bool wait: whether to wait for the experiment to complete
        :param bool verbose: if wait is True, verbose enables progress printout via tqdm
        :param kwargs: keyword arguments for the change_general_settings method (these will be applied prior to
            running the experiment)
        :return:
        """
        # ask if there's a way to specify the filename
        # todo store the filename
        # if experiment settings were specified perform change_experiment settings call
        self.logger.info('Executing experiment')
        self._api_put_and_parse(
            '/interfaces/iFlow/RunExperiment',
            kwargs,
            {
                0: 'Succeeded, experiment started',
                1: 'Failed due to active auto-shimming session',
                2: 'An experiment is already running',
                3: 'No response',
                4: 'Bad parameters',
                5: 'No such experiment',
            },
        )
        if wait is True:
            self.wait_for_experiment(verbose=verbose)
        return True

    def wait_for_experiment(self,
                            check_frequency: float = 1.,
                            verbose: bool = None,
                            ):
        """
        Waits for the experiment to complete.

        :param float check_frequency: query frequency in seconds
        :param verbose: enables progress printout via tqdm
        """
        self.logger.info('Waiting for experiment to complete')
        if verbose is None:
            verbose = self.PRINT_PROGRESS
        if verbose is True:
            # create tqdm object
            time.sleep(3.)  # todo remove when the API returns the correct number of scans by default
            n_scans = self.number_of_scans
            prog: tqdm.tqdm = tqdm.tqdm(
                desc='Running experiment',
                total=n_scans,
                unit='scans' if n_scans > 1 else 'scan',
            )
            status = self.experiment_status
            while status['ResultCode'] != 0:  # while the result code is not done
                status = self.experiment_status
                run_scans = status['NumberOfScansRun']
                if run_scans != prog.n:  # check if number has changed
                    prog.update(run_scans - prog.n)
                else:  # otherwise refresh
                    prog.refresh()
                time.sleep(check_frequency)
            prog.close()
        else:
            while self.experiment_status['ResultCode'] != 0:
                time.sleep(check_frequency)

    def cancel_experiment(self) -> bool:
        """Cancels the currently running experiment"""
        self.logger.info(f'Cancelling experiment')
        return self._api_put_and_parse(
            '/interfaces/iFlow/CancelExperiment',
            {},
            {
                0: 'Succeeded, experiment has been cancelled',
                1: 'Failed, experiment still running',
            },
        )

    @property
    def experiment_status(self) -> dict:
        return self._api_get('/interfaces/iFlow/ExperimentStatus')

    @property
    def last_experiment_jcamp(self) -> str:
        """Retrieves the FID in JCAMP-DX format for the last experiment"""
        # todo build parser for this JCAMP format
        return self.experiment_status['JDX_FileContents_TD']

    @property
    def complex_frequency_spectrum(self) -> dict:
        """returns a dictionary describing the complex frequency spectrum from the last experiment"""
        return self._api_get('/interfaces/Service/Fd')['ComplexFrequencySpectrum']

    @property
    def frequency_spectrum(self) -> Tuple[np.ndarray, np.ndarray]:
        """returns the frequency spectrum of the last experiment
        data is returned as a tuple of numpy arrays (ppm array, intensity array)
        """
        fd = self.complex_frequency_spectrum
        real = np.asarray(fd['RealPart'])  # retrieve the real part of the spectrum
        ppm = np.linspace(  # create ppm array
            fd['StartX'],
            fd['EndX'],
            real.shape[0]
        ) / self.spectrometer_frequency * 1e6  # scale from frequency to parts per million
        return ppm, real

    @property
    def complex_time_domain(self) -> dict:
        """returns a dictionary describing the complex time domain of the last experiment"""
        return nmr._api_get('/interfaces/Service/Td')['ComplexTimeDomain']

    @property
    def last_experiment_integrals(self) -> dict:
        """Retrieves the integrals from the last experiment"""
        return self.experiment_status['IntegralReport']

    @property
    def last_experiment_peaks(self) -> list:
        """Returns the peak list from the last experiment"""
        # todo build an auto-grouping algorithm to guess at multiplicity? (probably unnecessary) 
        return [peak['PeakLocation'] for peak in self.last_experiment_integrals['Integrals']]

    @property
    def last_experiment_timestamp(self) -> str:
        """Timestamp for the last experiment run"""
        return self.experiment_status['OriginalReceipt']['TimeStamp']

    @property
    def last_experiment_filename(self) -> str:
        """The JCAMP-DX filename of the last experiment"""
        return self.experiment_status['JDX_Filename']

    @property
    def last_experiment_settings(self) -> dict:
        """Settings for the last experiment run"""
        return self.experiment_status['OriginalReceipt']['Settings']

    @property
    def startup_test_status(self) -> dict:
        """Status of the spectrometer startup tests"""
        # todo mention the 102 percent complete status
        return self._api_get('/interfaces/iStatus/StartupTestStatus')

    @property
    def solvent_groups(self) -> list:
        """Solvents defined on the spectrometer"""
        return self._api_get('/interfaces/iStatus/Solvents')['SolventGroups']

    @property
    def available_solvents(self) -> List[str]:
        """list of available solvents for the selected nucleus"""
        return self.solvent_groups[self.solvent_group_index]['solvents']

    @property
    def spectrometer_status(self) -> dict:
        return self._api_get('/interfaces/iStatus/SpectrometerStatus')

    @property
    def spectrometer_frequency(self) -> float:
        """spectrometer frequency in Hz"""
        return self.spectrometer_status['SpectrometerFrequency']

    @property
    def general_experiment_settings(self) -> dict:
        return self._api_get('/interfaces/iFlow/ExperimentSettings')

    @general_experiment_settings.setter
    def general_experiment_settings(self, dct: dict):
        self.change_general_settings(**dct)

    def change_general_settings(self, **kwargs):
        """
        Changes the experiment settings using the provided dictionary.

        :param kwargs: keyword argument definitions of settings to change
        """
        return self._api_put_and_parse(
            '/interfaces/iFlow/ExperimentSettings',
            kwargs,
            {
                0: 'Succeeded, values updated',
                1: 'Failed'
            }
        )

    def get_experiment_settings(self, experiment: str = None) -> dict:
        """
        Gets the experiment settings for the specified experiment (e.g. 1D, COSY, JRES, etc.).

        :param experiment: experiment to retrieve settings for. If None, the current experiment is used
        :return: dictionary of experiment settings
        """
        if experiment is None:
            experiment = self.experiment
        return self._api_get(
            f'/interfaces/iFlow/Settings/{experiment}'
        )

    @property
    def current_experiment_settings(self) -> dict:
        """settings for the current experiment"""
        return self.get_experiment_settings()

    def change_experiment_settings(self, experiment: str = None, **kwargs):
        """
        Changes the settings of the specified experiment (e.g. 1D, COSY, JRES, etc.).
        To view the available experiment settings call `experiment_settings`.

        :param experiment: experiment name. If left as None, the current experiment is used
        :param kwargs: keyword argument, values to modify
        """
        if experiment is None:
            experiment = self.experiment

        current_settings = self._api_get(f'/interfaces/iFlow/Settings/{experiment}')

        # check that settings are valid
        if len(kwargs.keys() - current_settings.keys()) != 0:
            invalid = ', '.join([f'"{key}"' for key in kwargs.keys() - current_settings.keys()])
            raise KeyError(f'Invalid keys were passed for the experiment "{experiment}": {invalid}')
        current_settings.update(kwargs)

        # todo verify that this is the case for every experiment (or only 1D)
        # catch to properly set pulseangle (pulse width must be -1 in the put call)
        if experiment == '1D' and 'PulseAngle' in kwargs:
            if 'PulseWidth' not in kwargs:
                current_settings.update({'PulseWidth': -1})
        if 'ReceiverGain' in kwargs and current_settings['AutoGain'] is True:
            current_settings.update({'AutoGain': False})

        self._api_put_and_parse(
            f'/interfaces/iFlow/Settings/{experiment}',
            current_settings,
        )

    @property
    @_elaborate_setting_error
    def auto_baseline(self) -> bool:
        """whether auto-baseline is enabled on the spectrometer"""
        return self.current_experiment_settings['AutoBaseline']

    @auto_baseline.setter
    def auto_baseline(self, value: bool):
        self.change_experiment_settings(
            AutoBaseline=value
        )

    @property
    @_elaborate_setting_error
    def auto_gain(self) -> bool:
        """whether automatic gain is enabled on the spectrometer"""
        return self.current_experiment_settings['AutoGain']

    @auto_gain.setter
    def auto_gain(self, value: bool):
        self.change_experiment_settings(
            AutoGain=value
        )

    @property
    @_elaborate_setting_error
    def auto_phase(self) -> bool:
        """whether automatic phase correction is enabled"""
        return self.current_experiment_settings['AutoPhase']

    @auto_phase.setter
    def auto_phase(self, value: bool):
        self.change_experiment_settings(
            AutoPhase=value
        )

    @property
    @_elaborate_setting_error
    def export_filename(self) -> str:
        """the filename for exporting the next experiment to"""
        return self.current_experiment_settings['ExportFilename']

    @export_filename.setter
    def export_filename(self, value: str):
        self.change_experiment_settings(
            ExportFilename=value,
        )

    @property
    def peak_threshold_multiplier(self) -> float:
        """
        Use this method to adjust the threshold at which peaks are detected.
        When peaks are detected, they are reported in the Peaks array within ExperimentResults.
        The relationship between the threshold multiplier and the value is:
        PeakThresholdValue = PeakThresholdMultiplier * (detected noise level)
        """
        # ask if we can access the detected noise level
        return self._api_get('/interfaces/iFlow/PeakParameters')['PeakThresholdMultiplier']

    @peak_threshold_multiplier.setter
    def peak_threshold_multiplier(self, value: float):
        self._api_put_and_parse(
            '/interfaces/iFlow/PeakParameters',
            {'PeakThresholdMultiplier': value},
        )

    @property
    def detected_noise_level(self) -> float:
        """noise level detected by the spectrometer"""
        # ask if this value can be exposed directly to the JSON API (rather than being back-calculated)
        return self.experiment_status['PeakThresholdValue'] / self.peak_threshold_multiplier

    @property
    def signal_to_noise(self) -> float:
        """signal to noise of the last experiment run (may only be accessed if an experiment has been run)"""
        # retrieve last integrals
        integrals = self.last_experiment_integrals
        if len(integrals) == 0:
            raise ValueError('Signal to noise could not be calculated either because an experiment has not been run '
                             'or there are no detectable peaks.')
        # find the peak with the maximum intensity
        max_peak = max(
            integrals['Integrals'],
            key=lambda x: x['Integration']
        )
        # todo consider returning maximum and minimum S/N as a tuple
        return max_peak['PeakIntensity'] / self.detected_noise_level

    def signal_to_noise_targeted(self,
                                  target_ppm: float,
                                  # ppm_wiggle: float = 0.2,
                                  ) -> float:
        """
        Calculates the signal to noise of the targeted signal.

        :param target_ppm: ppm for the signal of interest
        :return: signal to noise ratio of the target signal
        """
        """
        :param ppm_wiggle: plus/minus ppm for the target signal (if no signal is found in this region, an error will
            be raised)
        """
        integrals = self.last_experiment_integrals
        if len(integrals) == 0:
            raise ValueError('Signal to noise could not be calculated either because an experiment has not been run '
                             'or there are no detectable peaks.')
        # todo consider implement wiggle
        peak_intensity = None
        for signal in integrals['Integrals']:
            if signal['RegionStart'] <= target_ppm <= signal['RegionEnd']:
                peak_intensity = signal['PeakIntensity']
                break
        if peak_intensity is None:
            raise ValueError(
                f'No peak matching {target_ppm} ppm could be identified from the integrals '
            )
        return peak_intensity / self.detected_noise_level

    @property
    def manual_integrals(self) -> dict:
        return self._api_get('/interfaces/iFlow/ManualIntegrals')

    @manual_integrals.setter
    def manual_integrals(self, value: dict):
        """
        Setting of the entire manual integral dictionary is supported, but is very format specific.

        The provided dictionary must be of the format

            {
                "Integrals": [
                    { "RegionEnd": float, "RegionStart": float },
                    ...,
                ],
                "ReferenceEnergy": float
            }
        """
        # typecheck
        if type(value) != dict:
            raise TypeError(f'The manual integral dictionary must be of type dict (provided: {type(value)})')
        keys = value.keys()
        # check for extraneous or missing keys
        expected = {"Integrals", "ReferenceEnergy"}
        if len(expected - keys) != 0:
            raise KeyError(f'Both "Integrals" and "ReferenceEnergy" keys must be in the provided dictionary.')
        if len(expected ^ keys) != 0:
            raise KeyError(f'Invalid keys were passed: {value.keys() - {"Integrals", "ReferenceEnergy"}}. '
                           f'Valid keys: "Integrals", "ReferenceEnergy"')
        for integral in value['Integrals']:
            if len(integral.keys() - {'RegionStart', 'RegionEnd'}) != 0:
                raise KeyError(f'Invalid keys were passed: {value.keys() - {"RegionStart", "RegionEnd"}}. '
                               f'Valid keys: "RegionStart", "RegionEnd"')
        self._api_put_and_parse(
            '/interfaces/iFlow/ManualIntegrals',
            value,
            {
                0: 'Set integrals successfully',
                1: 'Failed: Error using integral info',
            },
        )

    def calibrate_solvent(self) -> bool:
        """Initiate calibration of the solvent"""
        self.logger.info('Calibrating solvent')
        return self._api_put_and_parse(
            '/interfaces/iFlow/CalibrateSolvent',
            {},
        )

    @property
    def calibrate_solvent_status(self) -> dict:
        """Status of the solvent calibration"""
        return self._api_get(
            '/interfaces/iFlow/CalibrateSolvent'
        )

    def add_manual_integral(self, start: float, end: float) -> bool:
        """
        Adds a manual integral.

        :param start: start ppm
        :param end: end ppm
        """
        self.logger.info(f'Adding manual integral: {start}-{end}')
        # if retention of the previous integral is desired, the new integral must be appended
        current = self.manual_integrals
        current['Integrals'].append({
            'RegionEnd': end,
            'RegionStart': start,
        })
        return self._api_put_and_parse(
            '/interfaces/iFlow/ManualIntegrals',
            current,
            {
                0: 'Set integrals successfully',
                1: 'Failed: Error using integral info',
            },
        )

    def reset_manual_integrals(self) -> dict:
        """
        Resets the manual integrals for the instrument. The previously saved integrals are returned by the method.

        :return: previously set integrals
        :rtype: dict
        """
        self.logger.info('Resetting manual integrals')
        old = self.manual_integrals
        self._api_put_and_parse(
            '/interfaces/iFlow/ManualIntegrals',
            {
                'Integrals': [],
                'ReferenceEnergy': 1.,
            },
        )
        return old

    def shim(self,
             method: int = 1,
             wait: bool = False,
             solvent_shimming: bool = False,
             ) -> bool:
        """
        Shim the instrument.

        1 Quick auto-shim

        2 Medium auto-shim

        3 Full auto-shim

        :param method: shimming method index (0-3).
        :param bool wait: whether to wait for shimming to complete
        :param solvent_shimming: some magical parameter which may be required for good shimming
        """
        self.logger.info('Starting shim')
        if type(method) != int or not (1 <= method <= 3):
            raise ValueError(
                f'The shimming method {method} is invalid. Choose a value from 1-3'
                f'(quick, medium, full)'
            )
        self._api_put_and_parse(
            '/interfaces/iFlow/Shim',
            {
                "ShimmingMethod": method,
                "SolventShimming": solvent_shimming,
            }
        )
        if wait is not False:
            self.wait_for_shim()
        return True

    def adjust_parameters_and_shim(self,
                                   solvent_group_index: int = None,
                                   solvent: str = None,
                                   shim_method: int = 1,
                                   solvent_shimming: bool = False,
                                   ):
        """
        Adjusts spectrometer parameters, performs a shim, then restores the pre-shim parameters. Useful for performing
        a shim with a different lock nucleus. Note that the lock nucleus should already be set
        appropriately or the incorrect settings may be restored.

        :param solvent_group_index: solvent group index to use (see solvent groups)
        :param solvent: solvent to shim against
        :param shim_method: shim method to use (see shim)
        :param solvent_shimming: pass through to shim
        """
        if solvent_group_index is None:
            solvent_group_index = self.solvent_group_index
        if solvent is None:
            solvent = self.solvent
        self.logger.info('intializing a shim with temporary settings')

        # configure temporary setters
        temp_sg = TemporarilySet(self, 'solvent_group_index', solvent_group_index)
        temp_solv = TemporarilySet(self, 'solvent', solvent)

        # temporarily set the solvent group and solvent
        temp_sg.enter()
        temp_solv.enter()

        # perform shim
        self.logger.info('performing shim')
        self.shim(
            method=shim_method,
            wait=True,
            solvent_shimming=solvent_shimming,
        )

        self.logger.info('shim complete, restoring parameters ')
        temp_sg.exit()
        temp_solv.exit()

    def wait_for_shim(self,
                      check_frequency: float = 1.,
                      verbose: bool = None,
                      ):
        """
        Method to wait for a shim protocol to complete

        :param float check_frequency: query frequency
        :param verbose: whether to print out shim status using tqdm
        """
        self.logger.info('Waiting for shim to complete')
        if verbose is None:
            verbose = self.PRINT_PROGRESS
        if verbose is True:
            prog: tqdm.tqdm = None  # progress description changes multiple times
            while self.shim_status['ShimmingMethod'] != 0:
                status = self.shim_status
                # if the description has changed, close previous and create new instance
                if prog is None or prog.desc != status['ShimmingMessage']:
                    if prog is not None:  # close previous
                        prog.update(100 - prog.n)
                        prog.close()
                    prog = tqdm.tqdm(
                        desc=status['ShimmingMessage'],
                        total=100.,
                        unit='%',
                    )
                # if percent has changed, update; otherwise refresh
                if status['PercentComplete'] != prog.n:
                    prog.update(status['PercentComplete'] - prog.n)
                else:
                    prog.refresh()
                time.sleep(check_frequency)
            prog.close()
        else:
            while self.shim_status['ShimmingMethod'] != 0:
                time.sleep(check_frequency)

    def cancel_shim(self) -> dict:
        """Cancels an active shimming session"""
        self.logger.info('Cancelling shim')
        return self._api_put(
            '/interfaces/iFlow/Shim',
            {
                "ShimmingMethod": 0,
                "SolventShimming": False
            }
        )

    @property
    def shim_status(self) -> dict:
        """The status of the shim"""
        return self._api_get('/interfaces/iFlow/Shim')

    @property
    @_elaborate_setting_error
    def active_time_scan(self) -> float:
        """
        The time during which the spectrometer is busy acquiring and processing data.
        This depends on the Spectral Width, Number of points, and zero filling factor.
        """
        return self.general_experiment_settings['ActiveTimeScanInSeconds']

    @property
    @_elaborate_setting_error
    def apodization(self) -> float:
        """A mathematical function applied to the raw FID data."""
        return self.general_experiment_settings['Apodization']

    @apodization.setter
    def apodization(self, value: float):
        self.change_general_settings(Apodization=value)

    @property
    @_elaborate_setting_error
    def nucleus(self) -> str:
        return self.general_experiment_settings['Nucleus']

    @nucleus.setter
    def nucleus(self, value: str):
        # setting must be changed in the solvent group
        setattr(self, 'solvent_group', value)

    @property
    def available_nuclei(self) -> List[str]:
        """Available nuclei"""
        # todo consider refactoring once it's clear what exactly a solvent group is
        # exposed based on available solvents
        return [key['name'] for key in self.solvent_groups]

    @property
    def nuclei(self) -> List[str]:
        """available nuclei. Pending deprecation, use available_nuclei instead"""
        warnings.warn(
            'the nuclei attribute is pending deprecation, use available_nuclei instead',
            DeprecationWarning,
            stacklevel=2,
        )
        return self.available_nuclei

    @property
    @_elaborate_setting_error
    def lock_nucleus(self):
        """The target nucleus"""
        return self.general_experiment_settings['LockNucleus']

    @lock_nucleus.setter
    def lock_nucleus(self, value: str):
        available = self.available_lock_nuclei
        if value not in available:
            raise ValueError(
                f'The lock nucleus "{value}" is not one of the available nuclei on this spectrometer: '
                f'{", ".join(available)}'
            )
        self.change_general_settings(
            LockNucleus=value,
        )

    @property
    @_elaborate_setting_error
    def available_lock_nuclei(self) -> List[str]:
        """list of available nuclei for locking"""
        return self.general_experiment_settings['LockNucleusAvailable']

    @property
    @_elaborate_setting_error
    def digital_resolution(self) -> float:
        """
        The digital resolution is the sweep width (in Hz) divided by the total number of digital points, the sum of
        number of acquisition and zero filling points. Finer digital resolution enables greater definition of fine
        features.
        """
        return self.general_experiment_settings['DigitalResolutionInHz']

    @property
    @_elaborate_setting_error
    def experiment(self) -> str:
        """Experiment name"""
        # The experiment index is abstracted
        return self.general_experiment_settings['ExperimentName']

    @experiment.setter
    def experiment(self, value: str):
        # while the experiment name is abstracted, indicies may still be specified
        available = self.available_experiments
        if type(value) == str:  # catch for named experiment
            for ind, name in enumerate(available):  # iterate over the defined experiments
                if name == value:
                    value = ind
                    break
            if type(value) == str:
                raise ValueError(f'The provided string "{value}" is invalid. '
                                 f'Choose from {", ".join(available)}')
        if value >= len(available):
            raise IndexError(f'The provided index {value} is beyond that defined in the available experiment list '
                             f'({len(available) - 1})')
        self.change_general_settings(Experiment=value)

    @property
    @_elaborate_setting_error
    def available_experiments(self) -> List[str]:
        """List of available experiments"""
        return self.general_experiment_settings['ExperimentNameAvailable']

    @property
    def experiments(self) -> List[str]:
        """list of available experiments, pending deprecation, use available_experiments instead"""
        warnings.warn(
            'the experiments attribute is pending deprecation, use available_experiments instead',
            DeprecationWarning,
            stacklevel=2,
        )
        return self.available_experiments

    @property
    @_elaborate_setting_error
    def number_of_points(self) -> int:
        """The number of data points acquired by the spectrometer (a binary multiple of 1024)"""
        return self.general_experiment_settings['NumberOfPoints']

    @number_of_points.setter
    def number_of_points(self, value: int):
        if type(value) != int or value % 1024 != 0:
            raise ValueError(f'The provided value ({value}) is not an integer or not a multiple of 1024. ')
        self.change_general_settings(NumberOfPoints=value)

    @property
    @_elaborate_setting_error
    def number_of_scans(self) -> int:
        """number of scans in the acquisition"""
        return self.general_experiment_settings['NumberOfScans']

    @number_of_scans.setter
    def number_of_scans(self, value: int):
        if type(value) != int:
            raise ValueError(f'The provided value {value} is not an integer')
        if value < 1:
            raise ValueError(f'The provided value {value} must be greater or equal to 1')
        self.change_general_settings(NumberOfScans=value)

    @property
    @_elaborate_setting_error
    def peak_integration_method(self) -> int:
        """
        Peak integration method

        0: manual
        1: automatic
        """
        return self.general_experiment_settings['PeakIntegrationMethod']

    @peak_integration_method.setter
    def peak_integration_method(self, value: int):
        self.change_general_settings(PeakIntegrationMethod=value)

    @property
    @_elaborate_setting_error
    def pulse_width(self) -> float:
        """Length of the 90 degree pulse in microseconds"""
        return self.general_experiment_settings['PulseWidthInMicroseconds']

    @pulse_width.setter
    def pulse_width(self, value: float):
        self.change_general_settings(PulseWidthInMicroseconds=value)

    @property
    def pulse_angle(self) -> float:
        """pulse angle for the spectrometer"""
        return self.current_experiment_settings['PulseAngle']

    @pulse_angle.setter
    def pulse_angle(self, value: float):
        self.change_experiment_settings(**{
            'PulseAngle': value,
            # 'PulseWidth': -1,  # pulse width must be -1 to trigger a change in angle
        })

    @property
    @_elaborate_setting_error
    def receiver_gain(self) -> float:
        """Gain setting on the receiver. The receiver gain controls the attenuation of the receiver circuit."""
        return self.current_experiment_settings['ReceiverGain']

    @receiver_gain.setter
    def receiver_gain(self, value: float):
        # ask whether there is some check on the NMR-side for ludicrous values
        self.change_experiment_settings(ReceiverGain=value)

    @property
    def auto_gain(self) -> bool:
        """whether auto gain is enabled for the current experiment"""
        return self.current_experiment_settings["AutoGain"]

    @auto_gain.setter
    def auto_gain(self, value: bool):
        self.change_experiment_settings(AutoGain=value)

    @property
    @_elaborate_setting_error
    def scan_delay(self) -> float:
        """Scan delay in seconds. Allows for sample NMR relaxation between scans"""
        return self.general_experiment_settings['ScanDelayInSeconds']

    @scan_delay.setter
    def scan_delay(self, value: float):
        self.change_general_settings(ScanDelayInSeconds=value)

    @property
    @_elaborate_setting_error
    def solvent(self) -> str:
        """The selected solvent"""
        # suggest that there be a better way to access current solvent
        settings = self.general_experiment_settings
        return self.solvent_groups[  # accession of the solvent name is really weird.
            settings['SolventGroup']
        ][
            'solvents'
        ][
            settings['Solvent']
        ]

    @solvent.setter
    def solvent(self, value: Union[str, int]):
        if type(value) == str:  # if passed a string, find key or raise error
            available_solvents = self.available_solvents
            if value not in available_solvents:
                raise ValueError(f'The solvent {value} is invalid or not defined in the available solvents for this '
                                 f'nucleus. Choose from {", ".join(available_solvents)}')
            value = available_solvents.index(value)
        self.change_general_settings(Solvent=value)

    @property
    @_elaborate_setting_error
    def solvent_group_index(self) -> int:
        """Index of the solvent group (index in NMR.solvents)"""
        return self.general_experiment_settings['SolventGroup']

    @solvent_group_index.setter
    def solvent_group_index(self, value):
        self.change_general_settings(
            SolventGroup=value,
            LockNucleus=self.general_experiment_settings['LockNucleus'],  # todo remove this when API is fixed
        )

    @property
    @_elaborate_setting_error
    def solvent_group(self) -> str:
        """Solvent group (nucleus)"""
        return self.solvent_groups[self.general_experiment_settings['SolventGroup']]['name']

    @solvent_group.setter
    def solvent_group(self, value: Union[str, int]):
        if type(value) == str:
            solvents = [solvent_group['name'] for solvent_group in self.solvent_groups]
            if value not in solvents:
                raise ValueError(f'The solvent group {value} is invalid or not defined in the available groups for this '
                                 f'spectrometer. Choose from {", ".join([solvent["name"] for solvent in solvents])}.')
            elif solvents.count(value) != 1:
                raise ValueError(
                    f'There is more than one solvent with the name "{value}", please set the solvent by index'
                )
            else:
                value = solvents.index(value)
        self.solvent_group_index = value

    @property
    @_elaborate_setting_error
    def spectral_center(self) -> float:
        """The position, in the continuous spectrum, of the center of the desired spectral width (in ppm)."""
        return self.general_experiment_settings['SpectralCentreInPpm']

    @spectral_center.setter
    def spectral_center(self, value: float):
        self.change_general_settings(SpectralCentreInPpm=value)

    @property
    @_elaborate_setting_error
    def spectral_width(self) -> float:
        """
        The range of frequencies over which NMR signals are to be detected. It is the reciprocal of the sampling
        interval.
        """
        return self.general_experiment_settings['SpectralWidthInPpm']

    @spectral_width.setter
    @_elaborate_setting_error
    def spectral_width(self, value: float):
        self.change_general_settings(SpectralWidthInPpm=value)

    @property
    @_elaborate_setting_error
    def time_per_scan(self) -> float:
        """
        The amount of time to complete a single scan in seconds.
        Includes acquisition time, delay time, and processing overhead time.
        One scan is the single acquisition of an NMR spectrum.
        """
        return self.general_experiment_settings['TimePerScanInSeconds']

    @property
    @_elaborate_setting_error
    def total_duration(self) -> float:
        """The estimated time the entire experiment will take to run (all scans)"""
        return self.general_experiment_settings['TotalDurationInSeconds']

    @property
    @_elaborate_setting_error
    def zero_filling(self) -> float:
        """Zero filling factor. Increasing the zero filling enhances the digital resolution of the spectrum."""
        return self.general_experiment_settings['ZeroFillingFactor']

    @zero_filling.setter
    def zero_filling(self, value: float):
        self.change_general_settings(ZeroFillingFactor=value)


if __name__ == '__main__':
    nmr = NMR('10.10.1.50')
    # nmr.address = '10.10.1.100'
    # nmr.shim()
    # nmr.add_manual_integral(1., 1.5)
    # nmr.add_manual_integral(2.2, 3.)
    # nmr.add_manual_integral(7,7.5)
