import numpy as np
import pandas as pd
import nmrglue as ng 
import os
from scipy import optimize 
from nanalysis import NMR
import shutil 
import matplotlib.pyplot as plt
import time #LAB 1_2_2024

class COLLECT_NMR(object):

    def __init__(self, rxn_num=0, dir_NMRdata='./'):   #WHAT IS THIS POINTING TOO? -LAB..oh this is on standalone...?
        self.dir_NMRdata = dir_NMRdata
        self.rxn_num = rxn_num

        return

    def load_nmr(self, ip_address='169.254.30.54'):
        self.nmr = NMR(ip_address)
        print('i am in load nmr')
        print('last experiment settings')
        print(self.nmr.general_experiment_settings)
        
        return

    def check_spec(self):
        print('i am in check spec')
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
        with open(self.dir_NMRdata +'/'+f'experiment_data_{self.rxn_num:02d}.dx', 'at') as f:
            f.write(jcamp)   

        return    


    def save_experiment_offline(self): #LAB

        file_path, reaction_num = input('Enter a file path of nmr(SPACE)experiment#').split()
        src = file_path
        #using shutil has a MAX 260 character limit
        dist =  r'C:/Users/volkaa/Documents/CL/New_Master_4dren_7/FlowChemAutomation/NanalysisData/experiment_data_%s.dx' % (reaction_num)
        #dist =  './NanalysisData/experiment_data_%s.dx' % (reaction_num)  #THIS HAS TO BE PUT IN when you deploy
        shutil.copyfile(src, dist)
        return #LAB END    

    def wait_time(self):
        print('waiting set time to let fluid equilibrate in nmr')
        time.sleep(180.0)  # 120 should be 2 min and this should be fine. 
        print('sleep time done. now acquire nmr')

    def run(self):
        self.load_nmr()
        #print(self.nmr.general_experiment_settings['ReceiverGain'])

        self.check_spec()
        #self.nmr.shim(wait=True)
        
        # kwargs ={'NumberOfScans':16,'ReceiverGain':12.0} ## PUT THIS IN YML>???? LAB Jan 8_2023
        # self.nmr.run_experiment(**kwargs) #..LAB mod Jan6 2023

        # # modified = self.nmr.general_experiment_settings # LAB v2
        # # modified['ReceiverGain'] = 22.0  #LAB v2
        # # self.nmr.change_general_settings(**modified)  #LAB v2
        
        self.nmr.run_experiment()

        self.nmr.wait_for_experiment()
        self.save_experiment()
        
        print('just ran experiment settings')
        print(self.nmr.general_experiment_settings)
        
        return

    def run_nmr_offline(self):
        self.save_experiment_offline() #LAB
        return


#making class for runnning NMR processing, default values are values from yaml
class NMR_PROCESSING(object):

    def __init__(self,Init_Values):
        
        self.xe_ppm = Init_Values['nmr_values']['xe_ppm']
        self.xe = Init_Values['nmr_values']['xe']
        self.bsl_ppm = Init_Values['nmr_values']['bsl_ppm']
        self.xpk = Init_Values['nmr_values']['xpk']
        self.int_width = Init_Values['nmr_values']['int_width']
        self.ref_int = Init_Values['nmr_values']['ref_int']
        self.ref_peak = Init_Values['nmr_values']['ref_peak']
        self.rct_vol = Init_Values['reactor_values']['rct_vol']
        self.yieldpk_num = Init_Values['nmr_values']['yieldpk_num']
        self.yieldpk_den = Init_Values['nmr_values']['yieldpk_den']

        self.results_filepath = Init_Values['filepaths']['NMRresults']

        return

    def gen_df(self, dirpath):
        """new data frame code that processes nmr data and appends to already processed nmr data"""
        
        if not os.path.exists(os.path.join(self.results_filepath,'./nmrlog.csv')):
            cols = [str(x) for x in self.xpk]
            df = pd.DataFrame(columns=cols)
            df.insert(0, 'File', [], True)
            df.to_csv(os.path.join(self.results_filepath,'./nmrlog.csv'), index=False)
        else:
            df = pd.read_csv(os.path.join(self.results_filepath,'./nmrlog.csv'))
        
        old_filelist = df['File'].to_list()

        """version that just appends df line of newly processed nmr data to most recent df"""
        for file in os.scandir(dirpath): 
        #for filename in file_list:
            if file.name[:-3] not in old_filelist: #takes new data to process and appends to most recent dataframe
                
                int_list = self.integrate_normalize_NMR(file)
            
                row = [file.name[:-3]]
                for item in int_list:
                    row.append(item)
                df.loc[len(df.index)] = row

        df.to_csv(os.path.join(self.results_filepath,'./nmrlog.csv'),index=False) #df only records peak integration values with each filename

        return df

    def gen_df_yield(self, file_list):

        df = self.gen_df(file_list)
# july22-2024        df['Ratio'] =df[str(self.yieldpk_num)]/df[str(self.yieldpk_den)] 
        df['Ratio'] =df[str(self.yieldpk_num)]/df[str(self.yieldpk_den)]
        return df

   # added from peter to try and processed transient nmr data

    def peak_plot_lbs(ax, data, x1, x2):
        idx1 = np.abs(ax-)
   
    def plotting(self, file_list):
        nfiles = len(file_list)
        offset =0.001

        fig, ax = plt.subplots()
        
        for j in range(nfiles):
            fn = file_list[j]
            xax, tdata = proc_dx(fn, dicproc)
            tdata = tdata + offset*j
            for i in range(npks):
                plt.plot(xax, tdata, 'k-')
                peak_plot_lbs(xax, tdata, xpk[i] - int_width[i], xpk[i] + int_width[i])

        plt.gca().invert_xaxis()
        plt.xlim(10,-2)
        plt.ylim(-0.0005, 0.008)

    #TODO: add production rate as part of df results - need known amount internal standard/concentr?
    #TODO?: do we want to also add selectivity? 


    def integrate_normalize_NMR(self, file): 
    #returns integral list for xpk list     
        #run baseline correct 
        filename = file.name
        filepath = file.path

        self.phase_baseline_correct_NMR(filepath)
        #index over peaks in peak list
        int_list=[]
        
        fig, ax = plt.subplots()
        
        ax.plot(self.xax, self.tdata.real)
        x = self.xax
        y = self.tdata.real
        #np.savetxt('C:/Users/volkaa/Documents/CL/Master_Dren_NOV/FlowChemAutomation/testing_spec/NMR.csv',np.vstack((x,y)).T,delimiter=',')
        ax.plot(self.xax[self.xbsl], self.tdata[self.xbsl], 'ro')
        plt.xlim(self.xe)
        plt.ylim(-500, 20000) 
        plt.title('Phase and Baseline corrected with points')

        for i in range(len(self.xpk)):
            #set integration bounds based on peak location (need to change to make this more robust)
            xe = [self.xpk[i] - self.int_width/2., self.xpk[i] + self.int_width/2.]
            xe_pts = self.ppm_to_pts(self.xax, xe)
            #copy section of spectra
            # print('T DATA: ', self.tdata)
            yexp = self.tdata[xe_pts[0]:xe_pts[1]]
            yint = yexp.cumsum()
            yint = np.flip(yint)
            xexp = self.xax[xe_pts[0]:xe_pts[1]]
            plt.plot(xexp, yint/20., 'r') #commented out to remove integration lines on saved figure
            int_list.append(yexp.real.sum())

        plt.vlines(x = [4.25, 4.75, 5.25, 6.3, 6.8, 5.8], ymin = [0,0,0,0, 0, 0], ymax = [10000,10000,10000,10000,10000,10000],
           colors = 'purple',
           label = 'vline_multiple - full height')
        
        
        plt.savefig(os.path.join(self.results_filepath,'spectrum_%s.png'%filename[:-3]),bbox_inches='tight') #save figure as .png
        plt.close()

        #normalize peaks 
        indx = self.xpk.index(self.ref_int)
        scf = self.ref_peak / int_list[indx]
        int_list = scf * np.array(int_list)
        return int_list

    def phase_baseline_correct_NMR(self, filepath):
        dic, data, udic = self.read_dx_60MHz(filepath)
        #get chemical shift axis
        self.get_xax(udic)

        #select range for optimum ph1 phasing
        xe_pts = self.ppm_to_pts(self.xax, self.xe_ppm)
        self.roi = xe_pts ##double check
        self.tdata = ng.proc_base.fft(data)
        #get phase values from autophase 
        self.autoph_roi()
        #3phasingfunction-phase
        self.tdata = self.ph3(self.tdata, self.ph0, self.ph1, self.piv)
        #polynomial baseline correction: 
        self.xbsl = self.ppm_to_pts(self.xax, self.bsl_ppm)
        self.xbsl.sort()
        ybsl = self.tdata[self.xbsl]
        self.tdata = self.bsl_poly(self.xbsl, ybsl, 3)

        # fig, ax = plt.subplots()
        # ax.plot(self.xax, self.tdata.real)
        # ax.plot(self.xax[self.xbsl], self.tdata[self.xbsl], 'ro')
        # plt.xlim(self.xe)
        # plt.ylim(-100, 20000) 
        # plt.title('Phase and Baseline corrected with points.noint')
        # plt.show()

        return

    ###changeclassstruct# 
    def autoph_roi(self):
        """ autophase spectra using peaks in region of interest
        for ph1 phasing
        1. optimize ph0 (largest peak)
        2. set pivot to largest peak
            3. optimize peak max in roi """
        
        """ initialize parameters """
        self.ph0, self.ph1, self.piv = 0., 0., 0.
        
        """ optimize ph0 """
        result = optimize.minimize_scalar(self.opt_ph0)
        self.ph0=result.x
        
        """ set pivot point to largest peak """
        tdataph=self.ph3(self.tdata, self.ph0, self.ph1, self.piv)
        self.piv = np.argmax(tdataph.real)
        
        """ optimize ph1 using peak max in roi """
        result = optimize.minimize_scalar(self.opt_ph1_roi)
        self.ph1 = result.x
    
        return
            
        # """ set pivot point to largest peak """
        # tdataph = self.ph3(self.tdata, self.ph0, self.ph1, self.piv)
        # self.piv = np.argmax(tdataph.real)
        
        # """ optimize ph1 using peak max in roi """
        # result = optimize.minimize_scalar(self.opt_ph1_roi)
        # self.ph1 = result.x

    ##3 parameter phasing## 
    # Used in minimize functions opt_ph0 and opt_ph1_roi with different inputs...I think we should rewrite this so this is not the case
    #but am keeping for now since don't understand the phasing NMR world 
    #tldr: this function seems to take different inputs in the original code, so self is not used
    #optimizer so dont do self.data bc dont want to overwrite self.data used from the reading 60mhx function
    def ph3(self, data, ph0, ph1, piv):###does this USE DATA OR TDATA? tdata later on? 
        npt = data.shape[-1]
        xpt = np.linspace(1, npt, npt) - int(piv)
        phi = (ph0 + ph1 * xpt / npt) * np.pi / 180.
        apod = np.zeros(npt, dtype=complex)
        apod.real, apod.imag = np.cos(phi), -np.sin(phi)
        data = apod * data
        return data

    """ autoph_roi function """
    def opt_ph0(self, x):
        tdataph = self.ph3(self.tdata, x, self.ph1, self.piv)
        ytot = -1. * np.sum(tdataph.real)
        return ytot

    def opt_ph1_roi(self, x):
        tdataph = self.ph3(self.tdata, self.ph0, x, self.piv)
        yroi = tdataph[self.roi[0]:self.roi[1]]
        """ use signal max in roi"""
        ytot = -1. * np.sum(yroi.real)
        return ytot

    ##basic data processing functions##(?)#####
    def read_dx_60MHz(self, filepath):
        ####double check how data, udic returned#####
        """ read .dx data format 
        return: dic, data udic """
        dic, data = ng.fileio.jcampdx.read(filepath)
        udic = ng.fileio.jcampdx.guess_udic(dic, data)
        
        """ create complex array for FT """
        data = np.array(data)
        data = data[0,:] + 1.0j * data[1,:]

        """ fix parameters """
        offset = dic['.SOLVENTREFERENCE']
        offset = float(offset[0])
        sw = dic['$SW']
        sw = float(sw[0]) * udic[0]['obs']
        udic[0]['sw'] = sw
        udic[0]['offset'] = offset
        
        self.data = data
        return dic, data, udic

    """ polynomial fit to baseline """
    def bsl_poly(self, xbsl, ybsl, porder):
        """ xbsl - x baseline point
            ybsl - y baseline point
            porder polynomial order for fit
            return: baseline corrected data"""
        #data = self.tdata.copy()####
        npt = self.tdata.shape[-1]
        xpts = np.linspace(1,npt,npt)
        z = np.polyfit(xbsl, ybsl, porder)
        # print("I AM Z")
        # print(z)
        f = np.poly1d(z)
        bsl = f(xpts)

        return self.tdata-bsl

    def ppm_to_pts(self, xax, xe):
        """ convert ppm list to list of points """
        npts = len(xe)
        xe_pts = []
        for i in range(npts):
            indx=np.argmin(np.abs(xax-xe[i]))
            xe_pts.append(indx)
        # print(xe_pts)

        return xe_pts
        
    def get_xax(self, udic):
        """ create corrected axis from udic """
        npts = len(self.data)
        left_pt = udic[0]['offset'] + udic[0]['sw']/(2.*udic[0]['obs'])
        right_pt = udic[0]['offset'] - udic[0]['sw']/(2.*udic[0]['obs'])
        
        self.xax = np.linspace(right_pt, left_pt, npts)

        return
    
    # def xax_fix(self, tdata, ref_ppm):#TODO:check if this xax_fix ever needs to be called-dont see in original? 
    #    th = 0.8 * np.max(tdata.real)
    #    peaks = ng.peakpick.pick(tdata, th)
    #    xpeak = peaks['X_AXIS'].astype('int')
    #    xppm = self.xax[xpeak]
    #    delta = ref_ppm - np.min(xppm)
    #    self.xax = self.xax + delta
    #    return 



if __name__=='__main__':
    nmr = COLLECT_NMR()
    nmr.load_nmr()
    nmr.check_spec()