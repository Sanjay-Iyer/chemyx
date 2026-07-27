import time
import os
import numpy as np
import csv 
import datetime 
from nanalysis import NMR
import pandas as pd

class COLLECT_NMR(object):

    def __init__(self, timestamp=0, dir_NMRdata='./', numscans=2, ReceiverGain=12):  
        self.dir_NMRdata = dir_NMRdata
        self.timestamp = timestamp
        self.numscans = numscans
        self.ReceiverGain = ReceiverGain

        return

    def load_nmr(self, ip_address='169.254.30.54'):
        self.nmr = NMR(ip_address)
        print("I AM IN LOAD NMR")

        return

    def check_spec(self):
        print("I AM IN CHECK SPEC")
        if not self.nmr.ping():
            raise('Unable to reach spectrometer')
        else:
            print('Connected to spectrometer')

        if not self.nmr.rpc_enabled:
            raise('Remote control not enabled')

        if not self.nmr.startup_test_status:
            raise('Spectrometer status check failed')

        return

    def save_experiment(self):
        jcamp = self.nmr.last_experiment_jcamp
        # save jcamp
        #save spectra filename with # reaction on 
        with open(self.dir_NMRdata +'/'+f'experiment_data_{self.timestamp}.dx', 'at') as f:
            f.write(jcamp)   

        return    


    def run(self):
        
        self.load_nmr()
        self.check_spec()
        #self.nmr.change_general_settings(NumberOfScans=self.numscans)
        # kwargs = {'ReceiverGain':self.ReceiverGain}
        # self.nmr.run_experiment(*kwargs)

        modified = self.nmr.general_experiment_settings
        modified['ReceiverGain'] = 12.0
        self.nmr.change_general_settings(**modified)
        self.nmr.run_experiment()

        self.nmr.wait_for_experiment()
        self.save_experiment()
        
        return

temp_array = [] 
time_array = [] 

try:
    while True:
        #get 10 second intervals of NMR data and current temp 
        time.sleep(30)
        # #read temp and record to csv.. not using for LUCI test
        # temperature = reactor.read_temp(4)
        # temp_array.append(temperature)

        # current_time = datetime.datetime.now() ..lab
        # timestamp = current_time.timestamp()..lab
        # time_array.append(timestamp)..lab

        #collect NMR
        current_time = datetime.datetime.now()
        timestamp = current_time.timestamp()
        collect = COLLECT_NMR(timestamp=timestamp, dir_NMRdata=r'C:\Users\baldwila\OneDrive - United States Air Force\Documents\Baldwin Group\FlowAutomation_NMR\photochem_flow_nmr_reference', 
                              numscans=2, ReceiverGain=12)
        collect.run() 

except KeyboardInterrupt:
    print("stopped")
    pass 

# not using for the LUCI test
# df=pd.DataFrame({"temp": temp_array,"time": time_array})
# df.to_csv('C:/Users/baldwila/Documents/baldwin_subgroup/UNC_FlowChem_TransientNMR/Temp_Time/Temp_Time.csv', index=False)



