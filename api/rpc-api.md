
#### NOTE: This is a markdown file. Open it with a markdown viewer (like *retext* in Linux) for a better user experience 

# RPC API Documentation
2015-03-27 Mark Carlson (Initial version)

### Conventions
JSON is used to represent objects.

JSON discourages the use of lists since they can be dangerous to interpret in JavaScript, but the .net deserialization classes have a hard time using dictionaries. As a result, lists are used when appropriate:
```
   ["value0", "value1", ...]
```

### Data Structures
TBD.

### Port
Default RPC port is 5000

### Enabling API
To enable PUTs on */interfaces/iFlow* and */interfaces/iStatus* interfaces, make sure this line is in *config/pygui.cfg*:
```
RPC_API_ENABLED = True
```
Then, restart the GUI (if you had to change config/pygui.cfg) and go to 
```
Setup -> System -> Remote, and click "Enable".
```

## All Methods
* */interfaces/iStatus/OperationalMessages* GET
* */interfaces/iStatus/SpectrometerStatus* GET
* */interfaces/iStatus/PingSpectrometer* GET
* */interfaces/iStatus/StandbyMode* GET/PUT
* */interfaces/iStatus/RpcEnabled* GET
* */interfaces/iStatus/StartupTestStatus* GET
* */interfaces/iStatus/Solvents* GET
* */interfaces/iStatus/Solvents/<int:group_id\>* GET
* */interfaces/iFlow/ValidateExperimentSettings* PUT
* */interfaces/iFlow/ExperimentSettings* GET/PUT
* */interfaces/iFlow/RunExperiment* GET/PUT
* */interfaces/iFlow/CancelExperiment* PUT
* */interfaces/iFlow/CalibrateSolvent* GET/PUT
* */interfaces/iFlow/ExperimentStatus* GET
* */interfaces/iFlow/PeakParameters* GET/PUT
* */interfaces/iFlow/ManualIntegrals* GET/PUT
* */interfaces/iFlow/Shim* GET/PUT
* */interfaces/iFlow/Settings/1D* GET/PUT
* */interfaces/iFlow/Settings/DEPT* GET/PUT
* */interfaces/iFlow/Settings/Kinetics* GET/PUT
* */interfaces/iFlow/Settings/HSQC* GET/PUT
* */interfaces/iFlow/Settings/COSY* GET/PUT
* */interfaces/iFlow/Settings/JRES* GET/PUT
* */interfaces/iFlow/Settings/Nutation* GET/PUT
* */interfaces/iFlow/Settings/T1* GET/PUT
* */interfaces/iFlow/Settings/T2* GET/PUT
* */interfaces/Experiment/List* GET
* */interfaces/Experiment/Status* GET
* */interfaces/Experiment/Cancel* GET/PUT
* */interfaces/Experiment/<experimentName\>/Settings* GET
* */interfaces/Experiment/<experimentName\>/Start* PUT
* */interfaces/Experiment/Results* GET
* */interfaces/Experiment/Results/<resultName\>* GET
* */interfaces/Service/ShimTraces* PUT
* */interfaces/Service/ShimValues* GET/PUT
* */interfaces/Service/ShimNames* GET
* */interfaces/Service/Shims* GET
* */interfaces/Service/ShimFile* GET/PUT
* */interfaces/Service/ShimIDs* GET/PUT
* */interfaces/Service/ShimPower* GET
* */interfaces/Service/ShimCurrentSummary* GET
* */interfaces/Service/ArtificialGroundCurrent* GET
* */interfaces/Service/Acquire* GET/PUT
* */interfaces/Service/AutoGain* PUT
* */interfaces/Service/SaveResults* PUT
* */interfaces/Service/Metric* GET/PUT
* */interfaces/Service/LineWidths* GET/PUT
* */interfaces/Service/PidPower* GET
* */interfaces/Service/Fd* GET
* */interfaces/Service/Td* GET
* */interfaces/Service/TdStats* GET
* */interfaces/Service/ReferenceApodization* GET/PUT
* */interfaces/Service/ServiceMode* GET/PUT
* */interfaces/Service/DecimationMethod* GET/PUT
* */interfaces/Service/CancelExperiment* PUT
* */interfaces/Service/LastDataSets* GET/PUT
* */interfaces/Service/LedOverride* GET/PUT
* */interfaces/Service/AcquisitionLog/Enabled* GET/PUT
* */interfaces/Service/AcquisitionLog/Header* GET
* */interfaces/Service/AcquisitionLog/FormattedEntry* GET
* */interfaces/Service/AcquisitionLog/LockLevel* GET
* */interfaces/Service/AcquisitionLog/LockOffset* GET
* */interfaces/Service/AcquisitionLog/SignalPeakOffset* GET
* */interfaces/Service/Autoshim/Status* GET
* */interfaces/Service/Autoshim/Start* PUT
* */interfaces/Service/Autoshim/Cancel* PUT
* */interfaces/Service/Autoshim/Config* GET/PUT
* */interfaces/Service/Autoshim/LoadFiducialConfig* PUT
* */interfaces/Service/Autoshim/Log* PUT
* */interfaces/Service/Parameters/Signal* GET/PUT
* */interfaces/Service/Parameters/Lock* GET/PUT
* */interfaces/Service/SignalSettings/<setting\>* GET/PUT
* */interfaces/Service/FiducialConfigFile* GET/PUT
* */interfaces/Service/Temperatures/ControlBoard* GET
* */interfaces/Service/Temperatures/Enclosure* GET
* */interfaces/Service/Temperatures/Magnet/<which\>* GET
* */interfaces/Service/Temperatures/PIDPower/<which\>* GET
* */interfaces/Service/Temperatures/System* GET
* */interfaces/Service/Temperatures/Target* GET
* */interfaces/Service/RpcActive* GET
* */interfaces/Service/setRpcForced* PUT
* */interfaces/Script/Settings* PUT
* */interfaces/Script/Start* PUT
* */interfaces/Script/Status* GET
* */interfaces/Script/Results* GET
* */interfaces/Script/Results/<resultName\>* GET

#### PUTs to */interfaces/iStatus* and */interfaces/iFlow* are protected, and will return “403 Forbidden” if not the API is not enabled

## Method examples

### */interfaces/iStatus/OperationalMessages*

**GET**
```
{
  "messages": [
    {
      "Message": "Run an  (full)",
      "Type": "Autoshim"
    }
  ]
}
```
## */interfaces/iStatus/SpectrometerStatus*
**GET**
```
{
  "Drift": 0.0008557449111069529,
  "FirmwareVersion": "9.9.8",
  "Resolution": {
    "LineWidths": [
      {
        "Threshold": 5.0,
        "Width": 6.0272216796875
      },
      {
        "Threshold": 10.0,
        "Width": 4.1961669921875
      },
      {
        "Threshold": 50.0,
        "Width": 1.373291015625
      }
    ],
    "TimeStamp": "Tue Apr  7 10:09:07 2015"
  },
  "Sensors": {
    "ControlBoardTemperature": 36.0,
    "EnclosureTemperature": 28.100000381469727,
    "MagnetTemperature": 29.100000381469727
  },
  "SerialNumber": "mark12-04",
  "SoftwareVersion": "1.1.5 - 2851M",
  "SpectrometerFrequency": 60000133.12634938,
  "StandbyMode": false,
  "TimeStamp": "Tue Apr  7 10:14:43 2015"
}
```
#### Note: Linewidths are only in the dictionary if there has been an autoshim run

## */interfaces/iStatus/PingSpectrometer*
**GET**
```
{
  "connected": true
}
```
## */interfaces/iStatus/StandbyMode*
**GET**
```
{
  "StandbyMode": true
}
```
**PUT**
```
{
   "StandbyMode" : true
}
```
## */interfaces/iStatus/RpcEnabled*
**GET**
```
{
  "RpcEnabled": false
}
```
## */interfaces/iStatus/StartupTestStatus*
**GET**
```
{
  "ResultCode": 1,
  "PercentComplete": 50,
  "Message": "Waiting for magnet temperature"
}
```
## */interfaces/iStatus/Solvents*
**GET**
```
{
  "SolventGroups": [
    {
      "name": "(1H) Hydrogen",
      "solvents": [
        "D2O",
        "DMSO-d6",
        "Chloroform-d",
        "Methanol-d4",
        "Acetone-d6",
        "Acetonitrile-d3",
        "Benzene-d6",
        "TFA-d",
        "Ethanol-d6",
        "THF-d8"
      ]
    }
  ]
}
```
## */interfaces/iStatus/Solvents/<int:group_id\>*
**GET**
#### E.g. /interfaces/iStatus/Solvents/0
```
{
  "name": "(1H) Hydrogen",
  "solvents": [
    "D2O",
    "DMSO-d6",
    "Chloroform-d",
    "Methanol-d4",
    "Acetone-d6",
    "Acetonitrile-d3",
    "Benzene-d6",
    "TFA-d",
    "Ethanol-d6",
    "THF-d8"
  ]
}
```
## */interfaces/iFlow/ValidateExperimentSettings*
**PUT**
```
{
  "ActiveTimeScanInSeconds": 2.5559999644756317,
  "Apodization": 1.5,
  "DigitalResolutionInHz": 0.0762939453125,
  "Experiment": 1,
  "NumberOfPoints": 2048,
  "NumberOfScans": 1,
  "PeakIntegrationMethod": 1,
  "PulseWidthInMicroseconds": 16.628877639770508,
  "ReceiverGain": 20,
  "ScanInSeconds": 0.0,
  "Solvent": 8,
  "SolventGroup": 0,
  "Nucleus": "1H",
  "LockNucleus": "2H",
  "SpectralCentreInPpm": 5.0,
  "SpectralWidthInPpm": 22.0,
  "TimePerScanInSeconds": 2.5559999644756317,
  "TotalDurationInSeconds": 2.5559999644756317,
  "ZeroFillingFactor": 7.0
}
```
## */interfaces/iFlow/ExperimentSettings*
**GET/PUT**
```
{
  "ActiveTimeScanInSeconds": 2.5559999644756317,
  "Apodization": 1.5,
  "DigitalResolutionInHz": 0.0762939453125,
  "Experiment": 1,
  "ExperimentName": "1D",
  "NumberOfPoints": 2048,
  "NumberOfScans": 1,
  "PeakIntegrationMethod": 1,
  "PulseWidthInMicroseconds": 16.628877639770508,
  "ReceiverGain": 20,
  "ScanInSeconds": 0.0,
  "Solvent": 8,
  "SolventGroup": 0,
  "Nucleus": "1H",
  "LockNucleus": "2H",
  "SpectralCentreInPpm": 5.0,
  "SpectralWidthInPpm": 22.0,
  "TimePerScanInSeconds": 2.5559999644756317,
  "TotalDurationInSeconds": 2.5559999644756317,
  "ZeroFillingFactor": 7.0
}
```
## */interfaces/iFlow/ExperimentSettings*
**GET**
```
{
  "ActiveTimeScanInSeconds": 2.5559999644756317,
  "Apodization": 0.20000000298023224,
  "DigitalResolutionInHz": 0.0762939453125,
  "Experiment": 1,
  "ExperimentName": "1D",
  "ExperimentNameAvailable": [
      "Custom",
      "1D",
      "T1",
      "T2",
      "Nutation",
      "COSY",
      "JRES",
      "HSQC",
      "DEPT",
      "Kinetics"
  ],
  "Nucleus": "1H",
  "NucleusAvailable": [
      "1H",
      "13C"
  ],
  "LockNucleus": "2H",
  "LockNucleusAvailable": [
      "2H",
      "1H"
  ],
  "NumberOfPoints": 2048,
  "NumberOfScans": 1,
  "PeakIntegrationMethod": 0,
  "PulseWidthInMicroseconds": 16.628877639770508,
  "ReceiverGain": 14,
  "ScanDelayInSeconds": 0.0,
  "Solvent": 8,
  "SolventGroup": 0,
  "SpectralCentreInPpm": 5.0,
  "SpectralWidthInPpm": 22.0,
  "TimePerScanInSeconds": 2.5559999644756317,
  "TotalDurationInSeconds": 2.5559999644756317,
  "ZeroFillingFactor": 7.0
}
```
LockNucleusAvailable, NucleusAvailable, ExperimentNameAvailable are read-only parameters. LockNucleusAvailable differs dependending on the Nucleus selected. SolventGroup is the numeric equivalent to Nucleus. If the SolventGroup is not known use Nucleus. Nucleus will override SolventGroup selection.
## */interfaces/iFlow/RunExperiment*
**PUT**
```
{
    "ActiveTimeScanInSeconds": 2.5559999644756317,
    "Apodization": 0.20000000298023224,
    "DigitalResolutionInHz": 0.0762939453125,
    "Experiment": 1,
    "ExperimentName": "1D",
    "NumberOfPoints": 2048,
    "NumberOfScans": 1,
    "PeakIntegrationMethod": 1,
    "PulseWidthInMicroseconds": 16.628877639770508,
    "ReceiverGain": 14,
    "ScanDelayInSeconds": 0.0,
    "Solvent": 8,
    "SolventGroup": 0,
    "Nucleus": "1H",
    "LockNucleus": "2H",
    "SpectralCentreInPpm": 5.0,
    "SpectralWidthInPpm": 22.0,
    "TimePerScanInSeconds": 2.5559999644756317,
    "TotalDurationInSeconds": 2.5559999644756317,
    "ZeroFillingFactor": 7.0
}
```
## */interfaces/iFlow/RunExperiment*
**GET**
```
{
  "ExperimentNumber": 1,
  "ResultCode": 1,
  "Settings": {
    "ActiveTimeScanInSeconds": 2.5559999644756317,
    "Apodization": 0.20000000298023224,
    "DigitalResolutionInHz": 0.0762939453125,
    "Experiment": 1,
    "ExperimentName": "1D",
    "ExperimentNameAvailable": [
        "Custom",
        "1D",
        "T1",
        "T2",
        "Nutation",
        "COSY",
        "JRES",
        "HSQC",
        "DEPT",
        "Kinetics"
    ],
    "NumberOfPoints": 2048,
    "NumberOfScans": 1,
    "PeakIntegrationMethod": 1,
    "PulseWidthInMicroseconds": 16.628877639770508,
    "ReceiverGain": 14,
    "ScanDelayInSeconds": 0.0,
    "Solvent": 8,
    "SolventGroup": 0,
    "Nucleus": "1H",
    "NucleusAvailable": [
        "1H",
        "13C"
    ],
    "LockNucleus": "2H",
    "LockNucleusAvailable": [
        "2H",
        "1H"
    ],
    "SpectralCentreInPpm": 5.0,
    "SpectralWidthInPpm": 22.0,
    "TimePerScanInSeconds": 2.5559999644756317,
    "TotalDurationInSeconds": 2.5559999644756317,
    "ZeroFillingFactor": 7.0
  },
  "TimeStamp": "Fri Mar 27 11:09:26 2015" 
}
```
GET and PUT commands will launch the experiment. In the case of GET will use previously selected settings for the experiment.

## */interfaces/iFlow/CancelExperiment*
**PUT**
```
{}
```
## */interfaces/iFlow/CalibrateSolvent*
**PUT**
```
{}
```
**GET**
```
{
  “Message”: “Searching for Signal...”,
  “PercentComplete”: 20,
  “ResultCode”:0	
}
```
## */interfaces/iFlow/ExperimentStatus*
**GET**
```
{
    "JDX_FileContents_FD": "",
    "JDX_FileContents_TD": "",
    "JDX_Filename": "",
    "NumberOfScansRun": 0,
    "OriginalReceipt": {
        "ExperimentNumber": 1,
        "ResultCode": 1,
        "Settings": {
            "ActiveTimeScanInSeconds": 3.4232799857436262,
            "Apodization": 1.100000023841858,
            "DigitalResolutionInHz": 0.05261651426553726,
            "Experiment": 1,
            "NumberOfPoints": 2048,
            "NumberOfScans": 1,
            "PeakIntegrationMethod": 1,
            "PulseWidthInMicroseconds": 16.33333396911621,
            "ReceiverGain": 12,
            "ScanDelayInSeconds": 1,
            "Solvent": 0,
            "SolventGroup": 0,
            "SpectralCentreInPpm": 6,
            "SpectralWidthInPpm": 14,
            "TimePerScanInSeconds": 4.423279985743626,
            "TotalDurationInSeconds": 4.423279985743626,
            "ZeroFillingFactor": 7
        },
        "TimeStamp": "2017-12-20 10:26:02"
    },
    "PeakList": [
        4.986481653462923
    ],
    "PeakThresholdValue": 4.725261211395264,
    "Peaks": {},
    "ResultCode": 2
}
```
## */interfaces/iFlow/PeakParameters*
**PUT**
```
{
  "PeakThresholdMultiplier": 15.0
}
```
**GET**
```
{
  "PeakThresholdMultiplier": 15.000000239196005
}
```
## */interfaces/iFlow/ManualIntegrals*
**GET / PUT**
```
{
  "Integrals": [
    {
      "RegionEnd": 2.0,
      "RegionStart": 1.0
    }
  ],
  "ReferenceEnergy": 70386.53250336811
}
```
## */interfaces/iFlow/Shim*
**GET / PUT**
```
{
  "PercentComplete": 100,
  "ShimmingMessage": "Done",
  "ShimmingMethod": 0,
  "SolventShimming": false
}
```
PercentComplete is read-only

ShimmingMethod can be one of the following:
```
SHIM_NOT_RUNNING_COMPLETE = 0 (When setting, this cancels an autoshim in progress)
SHIM_QUICK = 1
SHIM_MEDIUM = 2
SHIM_FULL = 3
SHIM_CUSTOM_1 = 101
SHIM_CUSTOM_2 = 102
SHIM_CUSTOM_3 = 103
```
## */interfaces/iFlow/Settings/1D*
**GET / PUT**
```
{
  "AutoBaseline": false, 
  "AutoGain": true, 
  "AutoPhase": false, 
  "CurrentGain": 12.0, 
  "PulseAngle": 85.92698762441455, 
  "PulseWidth": 15.0, 
  "ReceiverGain": 12.0,
  "ExportFilename": ""
}
```
PulseAngle is read-only, unless PulseWidth is set to a value less than 0. 

To set the pulse angle instead of the pulse width, set PulseAngle to the desired value and set PulseWidth to -1. 

PulseAngle is in degrees.

PulseWidth is in microseconds.

If ExportFilename is set, experiment will be saved in the set filename

CurrentGain is read-only

## */interfaces/iFlow/Settings/DEPT*
**GET / PUT**
```
{
  "InterPulseDelay": 0.0035,
  "ThetaPulseAngle": 135,
  "ExportFilename": ""
}
```

## */interfaces/iFlow/Settings/Kinetics*
**GET / PUT**
```
{
  "GeometricFactor": 2,
  "MinimumTau": 0.1,
  "NumClusters": 10,
  "ReceiverGain": 12,
  "Tau": 5,
  "TimeEstimate": 87.29999399185182,
  "TotalWaitTime": 45,
  "UserWaitTimes": [],
  "WaitScalingFactor": 1,
  "WaitType": 0,
  "ExportFilename": ""
}
```
Read-only parameters: ReceiverGain, TimeEstimate, TotalWaitTime

## */interfaces/iFlow/Settings/HSQC*
**GET / PUT**
```
{
  "AutoWidthValue": 102,
  "BirdRelaxationDelay": 0.5,
  "BirdSandwichDelay": 0.00344,
  "ExportFilename": "",
  "F1Center": 50,
  "F1Width": 200,
  "F1WidthAuto": 1,
  "Hetero180Pulse": 0.00004,
  "Hetero90Pulse": 0.00002,
  "IneptDelay": 0.00172,
  "Points": 64,
  "PurgePulseWidth": 0.001,
  "Signal180Pulse": 0.00003,
  "Signal90Pulse": 0.0000157165288925171,
  "StartTime": 0,
  "TimeEstimate": 5364.591966732235,
  "TotalScans": 1024,
  "ExportFilename": ""
}
```
If ExportFilename is not empty, results will be saved.

Read-only parameters: 180Pulse, 90Pulse, Hetero180Pulse, Hetero90Pulse, CurrentGain, TotalScans, AutoWidthValue, WidthUsed

## */interfaces/iFlow/Settings/COSY*
**GET / PUT**
```
{
    "180Pulse": 30,
    "90Pulse": 16,
    "AutoSecond90": 1,
    "AutoSpectralWidth": 1,
    "AutoWidthValue": 14,
    "CurrentGain": 12,
    "Points": 64,
    "PulseSecond90": 30,
    "TotalScans": 256,
    "SpectralWidth": 12,
    "T1StartTime": 0.1,
    "WidthUsed": 14
}
```
Read-only parameters: 180Pulse, 90Pulse, CurrentGain, TotalScans, AutoWidthValue, WidthUsed

## */interfaces/iFlow/Settings/JRES*
**GET / PUT**
```
{
  "180Pulse": 30,
  "90Pulse": 16,
  "Auto180": 1,
  "CurrentGain": 12,
  "Points": 32,
  "Pulse180": 30,
  "TotalScans": 128,
  "SpectralWidth": 50,
  "T1StartTime": 0.1
}
```
Read-only parameters: 180Pulse, 90Pulse, CurrentGain, TotalScans

## */interfaces/iFlow/Settings/Nutation*
**GET / PUT**
```
{
  "180Pulse": 30,
  "90Pulse": 16,
  "CurrentGain": 12,
  "End": 50,
  "LockChannel": 0,
  "Points": 16,
  "Start": 1
}
```
Read-only parameters: CurrentGain

## */interfaces/iFlow/Settings/T1*
**GET / PUT**
```
{
  "180Pulse": 30,
  "90Pulse": 16,
  "CurrentGain": 12,
  "LinearDist": 1,
  "Points": 16,
  "SaveResults": 1,
  "T1Time": 0,
  "TauStart": 10,
  "TauStop": 2000
}
```
Read-only parameters: 180Pulse, 90Pulse, CurrentGain

## */interfaces/iFlow/Settings/T2*
**GET / PUT**
```
{
  "180Pulse": 30,
  "90Pulse": 16,
  "CurrentGain": 12,
  "IsManual": 0,
  "LinearDist": 1,
  "Points": 16,
  "SaveResults": 1,
  "T2Time": 0,
  "TauStart": 10,
  "TauStop": 7000
}
```
Read-only parameters: 180Pulse, 90Pulse, CurrentGain, IsManual

## */interfaces/Experiment/List* 
**GET**
```
[
    "1D",
    "BIRD",
    "COSY",
    "DEPT",
    "HSQC",
    "JRES",
    "Kinetics",
    "Nutation",
    "T1",
    "T2"
]
```

## */interfaces/Experiment/<experimentName\>/Settings* 
**GET**

This call will return an example settings to be used to start the experiment

<experimentName\> is one of the items returned by */interfaces/Experiment/List*

```
{
    "metadata": {
        "id": "1D",
        "nucleus": "1H",
        "resultName": "1D_Result",
        "type": "experiment"
    },
    "setup": {
        "instrument": {
            "activeSolvent": "D2O",
            "configGroup": "1H",
            "interscanTime": 6,
            "numPoints": 2048,
            "numScans": 1,
            "spectralCenter": 5,
            "sweepWidth": 12
        },
        "params": {
            "dummyScans": 0,
            "pulseAngleOrWidth": [
                90,
                "°"
            ],
            "receiverGain": [
                10,
                "AUTO"
            ]
        }
    }
}
```

## */interfaces/Experiment/<experimentName\>/Start* 
**PUT**

Use the settings structure obtained from Settings and complete with desired values

## */interfaces/Experiment/Status* 
**GET**

Use this call to get the status of the recently started experiment

## */interfaces/Experiment/Cancel* 
**GET / PUT**

Getting or putting any value will cancel all experiments running

## */interfaces/Experiment/Results* 
**GET**

After the Experiment is finished, the a GET will return the available results

```
[
    "DataSet"
]
```

## */interfaces/Experiment/Results/<resultName>* 
**GET**

Get a result by name. The format of the response depends on the type of result.

same as  */interfaces/Script/Results/<resultName\>*

## */interfaces/Service/LastDataSets*

Controls the LastDataSets Debug Flag which accumulate the last number of DataSets of the signal channel in memory and saves them to 1D JCAMP-DX files (.dx).

**GET**
```
{
 "LastDataSetsParams": {
        "LastDataSetsFlagEnabled": false,
        "MaxDataSets": 100,
        "NumberOfDataSets": 0
    }
}
```
Get the parameters of the Last DataSets Debug Flag

**PUT**
```
{
  "ClearList": false,
  "MaxDataSets": 20,
  "FilenamePrefix": "test_",
  "LastDataSetsFlagEnabled": false
}
```
The response of the PUT is the same as the GET. All values in PUT can all be omitted. Settings are applied in the order displayed above. If no change on the state of the flag is desired, the LastDataSetsFlagEnabled outght to be ommited. When the flag is enabled the list of dataset is cleared and starts accumulating DataSets in memory until the maximum number set is reached. Then the first values are discarded and the new values are added at the end. If then the flag is set to disabled the datasets are saved using the prefix selected when the flag is set to off. If no prefix is selected a default name will be used. MaxDataSets can be set while the flag is on or off and the list is resized accordingly. The same applies for ClearList parameter.

## */interfaces/Service/ShimTraces*
**PUT**
```
{
  "Traces": [
    123.4,
    234.5,
    345.6,
[...]
}
```
Returns:
```
{
  "Values": {
    "Success": true
  }
}
```
The number of items required in “Traces” is currently 64. Each item is a current value in mA.
/interfaces/Service/ShimValues
GET
```
{
  "Shims": [
    123,
    234,
    345,
    345,
    44,
[...]
}
```
The same order as /interfaces/Service/ShimNames

**PUT** (Option 1)
```
{
  "Shims": [
    123,
    234,
    345,
[...]
}
```
Returns:
```
{
  "Values": {
    "Reason": "Set",
    "Success": true
  }
}
```
The shims can be set this way, in which case they are assumed to be in the same order as /interfaces/Service/ShimNames

**PUT** (Option 2)
```
{
  "Shims": [
    “9”: 123,
    “10” : 321
}
```
Returns:
```
{
  "Values": {
    "Reason": "Set",
    "Success": true
  }
}
```
One or more shims can be set this way. The index for each shim is given.

## */interfaces/Service/ShimNames*
**GET**
```
{
  "Names": [
    "z0",
    "XU1",
    "AG1",
    "XU2",
[...]
}
```
Ordered the same as they are in the config file.

## */interfaces/Service/Shims*
**GET**
```
{
  "Shims": {
    "AG1": 345,
    "AG2": 0,
    "AG3": 44,
    "AG4": 0,
    "AG5": 16,
[...]
}
```
Alphabetical order.

## */interfaces/Service/ShimFile*
**GET**
```
{
  "ShimFile": "Shim_d2o.cfg"
}
```
Get the shim file in use.

**PUT** (Load shims)
```
{
  "ShimFile": "Shim_d2o_new.cfg"
}
```
Load shims from the specified file. To reload the current shims, set ShimFile to an empty string “”.

**PUT** (Save shims)
```
{
  "Save": "Shim_d2o_modified.cfg"
}
```
Save shims to the specified file. To save the shims to the last loaded file, use an empty string for the filename “”. This does not change the solvent config file.

## */interfaces/Service/ShimIDs*
**GET**
```
{
  "ShimIDs": [
    0,
    1,
    2,
[...]
}
```
Returns all shim IDs, organized by shim group.

**GET** (Shim Orders to Shim IDs)

Send this:
```
{
  "Orders": [0, 1, 2, 3]
}
```
Gets the shim IDs for the specified orders (zeroth, first, second, etc.) Returned values are in the same format as for the regular GET version.

**GET** (Shim Names to Shim IDs)
Send this:
```
{
  "Names": ["x", "y", "z", ...]
}
```
Gets the shim IDs for the specified gradient names. Returned values are in the same format as for the regular GET version. An error will be returned if any of the names are not found.

##*/interfaces/Service/ShimPower*
*GET*
```
{
  "TotalPower": 0.134
  "Resistance": [
    0.75,
    0.75,
    0.75,
[...]
}
```
Total Power: Returns the total power (in watts) of all traces

Resistance: The resistance of each trace, in ohms

##*/interfaces/Service/ShimCurrentSummary*
**GET**
```
{
  "MaxTrace": -160.68336037060962,
  "SumNeg": -2678.7519879924616,
  "SumPos": 1832.9352184696615,
  "SumSquared": 21234.1946854553725226
}
```
MaxTrace: The maximum current on any one trace

SumNeg: The sum of all negative currents

SumPos: The sum of all positive currents

SumSquared: The sum of the square of each current

##*/interfaces/Service/ArtificialGroundCurrent*
**GET**

Get the artificial ground current in milliamps. Optinally, send the number of averages to use while performing the measurement (default: 16.)

```
{
  "Averages": 16
}
```
Returns:
```
{
  "CurrentMa": 123.234
}
```
##*/interfaces/Service/Acquire*
**GET**
```
{
  "Status": {
    "InProgress": false,
    "LastAcquireSuccessful": true
  }
}
```
**PUT**
```
{}
```
All parameters are read-only. Write “{}” to start an acquisition if one was not in progress. 

Returns “Success” or “Error”.
```
{
  "Status": "Success"
}
```
## */interfaces/Service/AutoGain*
**PUT**
```
{}
```
Write “{}” to perform an autogain. Returns the information about the autogain procedure. If the “before” and “after” gain values are not the same, you may want to perform an acquisition, then run AutoGain again.
```
{
  "GainAfter": 10,
  "GainBefore": 10,
  "Metric": 2345.678
}
```
## */interfaces/Service/SaveResults*
**PUT**
```
{
  "Filename": "path/to/file.dx"
}
```
Save TD data to the specified filename (under data/). FD data will also be saved if the Core is configured to do so. 

Returns:
```
{
  "Status": "Success"
}
```
## */interfaces/Service/Metric*
**GET / PUT**
```
{
  "Metric": 7.72363805770874,
  "MetricSelection": 5
}
```
Metric is read-only

MetricSelection can be one of the following:
```
0 -> M2 (Power)
1 -> M2 (Real)
2 -> M2sqrt (Power)
3 -> M2sqrt (Real)
4 -> M2 (Power w/Penalty)
5 -> Entropy
6 -> Linewidths
7 -> Cumulative Dist.
8 -> Max Signal Level
9 -> Tail Linewidth
10 -> M2 (Power w/BW)
11 -> TD (Mag) Lorentzian
```
##*/interfaces/Service/LineWidths*
**PUT**
```
{
  "Thresholds": [5.0, 10.0, 50.0]
}
```
Result is the same as for **GET**, but with the requested thresholds instead of the default ones. Any number of thresholds is allowed, including none. The “Thresholds” parameter must still be sent even if the list is empty, however.

**GET**
```
{
  "LineWidths": [
    {
      "Threshold": 5.0,
      "Width": 6.0272216796875
    },
    {
      "Threshold": 10.0,
      "Width": 4.1961669921875
    },
    {
      "Threshold": 50.0,
      "Width": 1.373291015625
    }
  ],
}
```
## */interfaces/Service/PidPower*
**GET**
```
{
  "PidPower": 0.0
}
```
## */interfaces/Service/Fd*
**GET**
```
{
  "ComplexFrequencySpectrum": {
    "EndX": 924.9237090918695,
    "ImagPart": [
      0.45785951827748383,
      ...
    ],
    "Milpart": 59.999997450056185,
    "RealPart": [
      0.8923725191750032,
      ...
    ],
    "StartX": -325.00002853854755
  }
}
```
EndX and StartX are in Hz. Convert to PPM by dividing by Milpart.

##*/interfaces/Service/TdStats*
**GET**
```
{
  "LastLockLost": false, 
  "SignalLevelLow": false, 
  "TdPeak": 8776, 
  "TdPhase": 0.15985898695662884, 
  "TdRmsNoise": 37.479511559976096, 
  "TdSnr": 149.9174892836047
}
```

TdSnr is the average of the TD signal in the first 300uS divided by the TdRmsNoise (which is calculated using the last 20% of the TD signal.)

## */interfaces/Service/Td*
**GET**
```
{
  "ComplexTimeDomain": {
    "EndX": 1.6375999586307444,
    "ImagPart": [
      -31.772260748449707,
      ...
    ],
    "RealPart": [
      4515.322560732152,
      ...
    ],
    "StartX": 0.0
  }
}
```

## */interfaces/Service/ReferenceApodization*
**GET / PUT**
```
{
  "Enabled": true
}
```
## */interfaces/Service/ServiceMode*
**GET / PUT**
```
{
  "Enabled": true
}
```
This enables “service mode”, which is the same as when the service screen is in use. The lit ring is turned off.

## */interfaces/Service/DecimationMethod*
**GET / PUT**
```
{
  "Method": 0,
  "Attenuation": 0.85
}
```
Set the decimation method and related parameters. See Processing::decimation() for details. When setting, “Method” is a mandatory argument. All others are optional.

## */interfaces/Service/CancelExperiment*
**PUT**
```
{}
```
Cancel the currently running experiment (or script.)

## */interfaces/Service/LedOverride*
**GET / PUT**
```
{
  "Blue": 0.0,
  "BlueFade": false,
  "BlueRotate": false,
  "Green": 0.0,
  "GreenFade": false,
  "GreenRotate": false,
  "Red": 0.0,
  "RedFade": false,
  "RedRotate": false,
  "override": false
}
```
Red, Green, and Blue are values from 0 to 1.0. The Fade and Rotate Settings override the individual colour settings. “override” is used to enable/disable the use of the settings.

## */interfaces/Service/AcquisitionLog/Enabled*
**GET / PUT**

Get or set whether the acquisition log is enabled or not.

```
{
  "Enabled": true,
}
```

## */interfaces/Service/AcquisitionLog/Header*
**GET**  

The header for the formatted log entries.

```
{
  "Header": "Time, TX Frequency, Signal Peak Offset, Last Lock Offset, Lock Level, 50%BW, M2Sqrt(Pow), Magnet Avg Temp, Magnet 1 Temp, Magnet 2 Temp, Ambient Temp, System Temp, Amps Temp, PID Power 0, PID Power 1, Artificial Ground, TD Phase [0], TD RMS Noise",
}
```
## */interfaces/Service/AcquisitionLog/FormattedEntry*
**GET**

A formatted log entry. This matches the fields in the Header. Note: The time field will not be accurate if the frequency log feature is not enabled. Enabling the frequency log feature sets the “start time”.

```
{
  "Entry": "0.000, 60000146.307160, -12.4971852, 0.0173652, 888.186523, 2.631, 3.953, 30.00000, 30.00000, 30.00000, 29.00000, 35.00000, 35.00000, 0, 0, 688.30667, -1.006, 3.492",
}
```

## */interfaces/Service/AcquisitionLog/LockLevel*
**GET**

An item in the acquisition log. Get the lock level.

```
{
  "Level": 888.3128662109375,
}
```
## */interfaces/Service/AcquisitionLog/LockOffset*
**GET**

An item in the acquisition log. Get the difference (in Hz) between where the frequency agility lock signal was expected, and where it actually was found.

```
{
  "Offset": 0.002,
}
```

## */interfaces/Service/AcquisitionLog/SignalPeakOffset*
**GET**

An item in the acquisition log. Get the raw offset (in Hz) of the peak from the center of the signal spectrum (magnitude mode.) This is without the spectral center applied.

```
{
  "Offset": 0.01,
}
```
## */interfaces/Service/Autoshim/Status*
**GET**
```
{
  "PercentComplete": 100,
  "ShimmingMessage": "Done",
  "ShimmingMethod": 0,
  "ShimmingResultCode": 0,
  "ShimsRemaining": 0,
  "SolventShimming": false
}
```
ShimmingResultCode is in AutoShim.hpp:

    enum AutoShimReturnCode

```
    {
        /// Failure
        AUTO_SHIM_EXCEEDED_MAX_SHIM_VALUE = 0,
        /// Failure
        AUTO_SHIM_EXCEEDED_MIN_SHIM_VALUE,
        /// Failure
        AUTO_SHIM_CURRENT_OVERFLOW,
        /// Failure
        AUTO_SHIM_METRIC_OVERFLOW,
        /// Not used by this class, used by the GUI
        AUTO_SHIM_NOT_IN_PROGRESS,
        /// Halving passes remain
        AUTO_SHIM_IN_PROGRESS,
        /// Success
        AUTO_SHIM_COMPLETE,
        /// Failure
        AUTO_SHIM_FAILURE,
        /// Not used by this class, used by the GUI
        AUTO_SHIM_ACQUIRE_ABORTED,
        /// Not used by this class, used by the GUI
        AUTO_SHIM_LOCK_ERROR,
        /// Not used by this class, used by the GUI
        AUTO_SHIM_COULD_NOT_START,
        /// Not used by this class, used by the GUI
        AUTO_SHIM_SIGNAL_ERROR,
    };
```

## */interfaces/Service/Autoshim/Start*
**PUT**
```
{
  "File": "autoshim_coarse.cfg",
  "ShimIds": [0, 1, 2, ...],
  "MetricSelection": 5,
  "UseFiducial": true
}
```
Starts an autoshim. Returns the same as /interfaces/Service/Autoshim/Status.

```
File: The autoshim config file to use
ShimIds: The IDs of the shims to use
MetricSelection: The same options as in /interfaces/Service/Metric
UseFiducial: If False, do not use fiducial settings, create the reference filter, or do a drift check
```

## */interfaces/Service/Autoshim/Cancel*
**PUT**
```
{}
```
**PUT**ting anything cancels the autoshim in progress. 

Returns the same as /interfaces/Service/Autoshim/Status.

## */interfaces/Service/Autoshim/Config*
**GET / PUT**
```
{
  "M2Threshold": 3.0,
  "M2Bandwidth": 1200.0,
  "M2TailPenalty": 0.1,
  "M2SkewPenalty": 2.9,
}
```
When setting parameters with PUT, not all parameters need to be included. If a parameter is not included, it will be left as-is.

## */interfaces/Service/Autoshim/LoadFiducialConfig*
**PUT**
```
{}
```

Load the fiducial configuration.

## */interfaces/Service/Autoshim/Log*
**PUT**
```
{
  "ShimType": "Quick",
  "Duration": 123.4
}
```

Add an entry to the shim log. ShimType is a short description of the type of shim. E.g. “Site Installation”, “Quick”, “Medium”, “Full”. 

## */interfaces/Service/Parameters/Signal*
**GET / PUT**
```
{
  "Interscan": 4.7,
  "SweepWidth": 80.0,
}
```
When setting parameters with PUT, not all parameters need to be included. If a parameter is not included, the rest will be left as-is.

Todo: Fill this out more, so it matches the items on the service screen.

## */interfaces/Service/Parameters/Lock*
**GET / PUT**

Same as /interfaces/Service/Parameters/Signal

## */interfaces/Service/SignalSettings/<setting\>*

All items at this interface operate the same way. You can get or set a signal acquisition setting. Currently available settings: (value type is in brackets)

PulseAngle (double): The angle of the first pulse in the pulse sequence, in degrees

PhaseLO1 (double): The first order phase, in degrees

**GET / PUT**

(e.g. /interfaces/Service/SignalSettings/PulseAngle)

```
{
  "Value": 90.0,
}
```
## */interfaces/Service/FiducialConfigFile*

Get/set items in the fiducial config file (directly.) This does not affect the currently loaded configuration, even if the currently loaded configuration is the fiducial config.

**GET / PUT**
```
{
  "180PulseTime": 40.0,
  "90PulseTime": 20.0,
  "Amplitude": 9.0,
  "Board": 2,
  "DampingFactor": 0.03999999910593033,
  "DeadTime_12Mhz": 0.00017499999376013875,
  "DeadTime_12Mhz_24bit": 0.00026500000967644155,
  "DeadTime_3Mhz": 0.00026500000967644155,
  "GyromagneticRatio": 42575500.0,
  "InterscanTime": 3.299999952316284,
  "LockSNRSpec": 2.4000000953674316,
  "Nucleus": 1,
  "NumberOfPoints": 5120,
  "PhaseCycling": 0,
  "PhaseLO1": 0.0,
  "PulseOnTime": 1.5e-05,
  "ReceiverGain": 11.0,
  "SweepWidth": 23,
  "TxBias": 751.0,
  "UserDelay": -1.0,
  "ZeroingFactor": 7.0
}
```
A **GET** returns all of the items above. When PUTting, you can omit items that you wish to leave alone.

## */interfaces/Service/Temperatures/ControlBoard*
**GET**

Get the temperature of the shim control board, in degrees Celsius.

```
{
  "Temperature": 1.234
}
```

## */interfaces/Service/Temperatures/Enclosure*
**GET**

Get the temperature of the enclosure, in degrees Celsius.

```
{
  "Temperature": 1.234
}
```
## */interfaces/Service/Temperatures/Magnet/<which\>*
**GET**

Get the temperature of the one of the magnet temperature sensors, in degrees Celsius. <which\> can be 1 or 2

```
{
  "Temperature": 1.234
}
```

## */interfaces/Service/Temperatures/PIDPower/<which\>*
**GET**

Get one of the the PID power readings from the temperature controller. <which\> can be 0 or 1

```
{
  "Power": 1.234
}
```

## */interfaces/Service/Temperatures/System*
**GET**

Get the temperature of the first (or only) RF board, in degrees Celsius.

```
{
  "Temperature": 1.234
}
```

## */interfaces/Service/Temperatures/Target*
**GET**

Get the magnet target temperature, in degrees Celsius.

```
{
  "Temperature": 1.234
}
```

## */interfaces/Service/RpcActive*
**GET**

Get if the it is possible to send and receive RPC commands. Checks if Core is connected and RPC license is present or, Core All Features present and Enabled in config file, and Checks if RPC is Enabled in the PyGUI, or Check if RPC is forced.
This command is never blocked.

```
{
  "RpcActive": True
}
```

## */interfaces/Service/setRpcForced*
**PUT**

Send an JSON object with an encoded Base64 *Packet* with a Nounce and another JSON object requesting to force RPC activation. It will force RPC to be activated whether it is licensed, enabled by license, in the config file or in the GUI or not. It requires access to the secret key to sign the message. The server must be started before the client for the command to be accepted. The JSON object must also contain a *Digest* with a Base64 encoded HASH of the signed packed with HMAC and SHA512 algorithms. This command is for Nanalysis internal use only.
This command is never blocked.


```
Put:
{
   "Packet": "......",
   "Digest": "......"
}

Response:
{
  "Success": True,
  "Message": {"Enabled": True}
}
```

## */interfaces/Script/Settings*
**GET / PUT**

When setting, you can specify the ScriptFilename, which will return the default “Config” for that script. Setting the “Config” here does not take effect. Do that in /interfaces/Script/Start instead.

ScriptRepository options are "Internal" and "User".

```
{
  "Config": {
    "Interpulse Delay (ms)": 3.5,
    "Theta Pulse Angle (deg)": 135.0
  },
  "ScriptFilename": "examples/dept.lua",
  "ScriptRepository": "User",
  "SkipReset": false
}
```

## */interfaces/Script/Start*
**PUT**

**Option 1:** Specify a filename (files are under scripts/ directory)

See /interfaces/Script/Settings for "ScriptRepository" options.

```
{
  "ScriptFilename": "examples/dept.lua",
  "ScriptRepository": "User",
  "SkipReset": false,
  "Config":
  {
    "Interpulse Delay (ms)": 3.123,
    "Theta Pulse Angle (deg)": 45.5
  }
}
```

**Option 2:** Send a base-64 encoded script and run it (no line-wrapping of the data)

```
{
  "ScriptData": base-64 encoded script data... 
}
```

Response:

```
{
  "success": true,
}
```

If *SkipReset* is true, all results from previous the script will not be deleted.

## */interfaces/Script/Status*
**GET**

There may be additional key/values added to this in the near future.

```
{
  "status": {
    "CurrentLine": 3,
    "LastStartedScript": "shim_current.lua",
    "LogMessages": [
      {
        "Message": "Error loading shim_currentx.lua:\ncannot open scripts/shim_currentx.lua: No such file or directory",
        "Timestamp": "2016-09-12 16:21:36.915501",
        "Type": "Error"
      }
    ],
    "Message": "",
    "Progress": 0.0,
    "ProgressMessage": "",
    "Running": false
  }
}
```

## */interfaces/Script/Results*
**GET**

Get a list of result names

```
[
  "TD SNR"
]
```

## */interfaces/Script/Results/<resultName\>*
**GET**

Get a result by name. The format of the response depends on the type of result.

Arguments:

* *format*: The result format. There are different options for all of the data types
* * DataSet1D supports: *json* (default,) *jdx*, *csv*, and *raw*
* * Results2D supports: *jdx* (default)
* *item:* The part of the result to get
* * DataSet1D supports: *x* and *y*
* * Results2D supports: *2D*, *x*, and *y*
* *type:* The format type supports several options for the type of data to obtain
* * *spectrum:* The frequency domain data
* * *fid:* The time domain data
* * *rawfid:* The time domain data (unprocessed)

Todo: Document all possible return types.

Example DataSet:

http://localhost:5000/interfaces/Script/Results/OneDdataSet?format=jdx&type=fid

GET: (example: DataString)

```
{
  "Name": "TD SNR", 
  "Value": "130.174667"
}
```
