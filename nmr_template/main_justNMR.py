#import reactor_commands as reactor
import time
#import nmr
import nmr_copy as nmr
#import edbo_manager
#import valves
import yaml
#import automate
import os
import numpy as np
#from edbo.plus.optimizer_botorch import EDBOplus

class CAMPAIGN(object):

    def __init__(self,ymlpath):
        '''
        Initialize campaign
        Inputs: ymlpath - path to .yml input file (DEFAULT to current directory)
        Returns: None
        '''
        
        #read input file
        with open(ymlpath,'r') as file:
            self.Init_Values = yaml.safe_load(file)

        self.filepaths = self.Init_Values['filepaths']
        #check for directories: NMR data, EDBO data, archiving directory, etc., if not found, make the directory
        if not os.path.isdir(self.filepaths['NMRdata']):
            os.mkdir(self.filepaths['NMRdata'])
        # if not os.path.isdir(self.filepaths['EDBOdata']):
        #     os.mkdir(self.filepaths['EDBOdata'])
        # if not os.path.isdir(self.filepaths['EDBOarchives']):
        #     os.mkdir(self.filepaths['EDBOarchives'])
        if not os.path.isdir(self.filepaths['NMRresults']):
            os.mkdir(self.filepaths['NMRresults'])

        self.nmr_ = nmr.NMR_PROCESSING(self.Init_Values)
      
 
    def run(self):
        
        df_NMR = self.nmr_.gen_df_yield(r'C:\Users\baldwila\Documents\baldwin_subgroup\nmrglue_testing')
        #df_NMR = self.nmr_.gen_df_yield(self.filepaths['NMRdata'])
        df_NMR2 = self.nmr_.plotting(r'C:\Users\baldwila\Documents\baldwin_subgroup\nmrglue_testing')    
      
        return 

    
    def run_offline(self):#This run offline mode will not work since the state checks are still in the init of the class. todo future move these checkcs outside of init for now commented out
        '''
        Function to begin campaign offline.
        Inputs: numreactions - number of reactions set in yaml file
        Return: None
        '''
        numreactions = self.Init_Values['campaign_values']['numrxns']

        for rxns in range(0,numreactions): #loop starts at 1 because 0 is the seed experiment
           
            ##### LAB
            a = nmr.COLLECT_NMR(rxn_num=rxns, dir_NMRdata=self.filepaths['NMRdata'])
            a.run_nmr_offline()
            
            #NMR processing 
            df_NMR = self.nmr_.gen_df_yield(self.filepaths['NMRdata'])
            
            #run EDBO and get next suggested experiments (and archived predictions)
            self.edbo_.send_nextround_to_vapourtec_from_NMRdf(df_NMR)

            # while self.valve_state.autosample(): #wait until all NMR spectra is taken and analyzed and valve state switches backs
            #     pass

            print('Ready to send next experiment(s) to Vapourtec.') #go to top of loop

        return 

    
    

# if __name__ == "__main__":
#     ymlpath = './InitValues.yml'
#     campaign = CAMPAIGN(ymlpath)
#     campaign.run()
#     # campaign.turnoff()
#     # #campaign.run_offline()



ymlpath = 'C:/Users/baldwila/Documents/baldwin_subgroup/UNC_FlowChem_TransientNMR/InitValues.yml'
campaign = CAMPAIGN(ymlpath)
campaign.run()