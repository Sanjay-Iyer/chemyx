import matplotlib.pyplot as plt
import numpy as np

import sys
import os
# nmrglue directory
pydir1='C:\Users\baldwila\Documents\baldwin_subgroup\nmrglue_testing'
sys.path.append(pydir1)
# nmrglue v0.9
import nmrglue as ng
import pandas as pd

from tkinter import filedialog
from tkinter import *

# import pickle
import copy
from scipy import optimize

import warnings
warnings.filterwarnings("ignore")

%matplotlib nbagg


""" functions """

def getfndir():
    """ get directory name """
    root = Tk()
    root.withdraw()
    msg1='Choose data directory: '
    print(msg1)
    fndir=filedialog.askdirectory()
    print(fndir)
    return fndir

def read_dx_60MHz(fn):
    """ read .dx data format 
    return: dic, data udic """
    dic, data = ng.fileio.jcampdx.read(fn)
    udic = ng.fileio.jcampdx.guess_udic(dic, data)
    
    """ create complex array for FT """
    tdata=np.array(data)
    tdata=tdata[0,:] + 1.0j*tdata[1,:]
    
    """ fix parameters """
    offset = dic['.SOLVENTREFERENCE']
    offset = float(offset[0])
    sw = dic['$SW']
    sw = float(sw[0])*udic[0]['obs']
    udic[0]['sw'] = sw
    udic[0]['offset'] = offset
    
    return dic, tdata, udic


""" automatic baseline detection"""
def abd(data, nsec, nf, nwin):
    # auto baseline detection
    # input: data, nsec (# baseline sections (32))
    #   nf (noise factor (3), nwin: window width (20))
    # output: xbsl, ybsl
    ndata=int(data.shape[0]/nsec)
    npts=data.shape[0]
    sigma=np.zeros(nsec)
    yt=np.zeros(npts)
    for i in range(nsec):
            tdata=data[i*ndata:(i+1)*ndata]
            sigma[i]=np.max(tdata)-np.min(tdata)
    nl=np.min(sigma)               # noise level
    # find baseline points
    for i in range(nwin+1, npts-(nwin+1), 1):
        tdata=data[int(i-nwin/2):int(i+nwin/2)]
        tsigma=np.max(tdata)-np.min(tdata)
        if tsigma < nl*nf:
            yt[i]=data[i]
    # --- get xbsl and ybsl 
    xbsl=np.array([])
    ybsl=np.array([])
    for i in range(npts):
        if yt[i] != 0.:
            xbsl=np.append(xbsl, i)
            ybsl=np.append(ybsl, yt[i])
    return xbsl, ybsl

""" polynomial fit to baseline """
def bsl_poly(data, xbsl, ybsl, porder):
    """ xbsl - x baseline point
        ybsl - y baseline point
        porder polynomial order for fit
        return: baseline corrected data"""
    npt = data.shape[-1]
    xpts=np.linspace(1,npt,npt)
    z = np.polyfit(xbsl, ybsl, porder)
    f = np.poly1d(z)
    bsl = f(xpts)
    return data-bsl

def ppm_to_pts(xax, xe):
    """ convert ppm list to list of points """
    npts = len(xe)
    xe_pts = []
    for i in range(npts):
        indx=np.argmin(np.abs(xax-xe[i]))
        xe_pts.append(indx)
    return xe_pts
    
def get_xax(data, udic):
    """ create corrected axis from udic """
    npts = len(data)
    left_pt = udic[0]['offset'] + udic[0]['sw']/(2.*udic[0]['obs'])
    right_pt = udic[0]['offset'] - udic[0]['sw']/(2.*udic[0]['obs'])

    xax = np.linspace(right_pt, left_pt, npts)
    return xax

def xax_fix(xax, tdata, ref_ppm):
    th = 0.8*np.max(tdata.real)
    peaks = ng.peakpick.pick(tdata, th)
    xpeak = peaks['X_AXIS'].astype('int')
    xppm=xax[xpeak]
    delta = ref_ppm -np.min(xppm)
    xax = xax + delta
    return xax

def pickplot(ax1, tdata, th):
    peaks = ng.peakpick.pick(tdata, th)
    xpeak = peaks['X_AXIS'].astype('int')
    xppm=ax1[xpeak]
    toff=0.01*max(tdata)
    # --- plots
    plt.plot(ax1, tdata)
    plt.plot(ax1[xpeak], tdata[xpeak]+toff, 'r|')
    ax1[xpeak]
    return peaks, xpeak

""" set directory """
dirproj = getfndir()

""" get data file names """
path=dirproj
fnamef = os.listdir(path)

""" select files of interest (foi) for analysis"""
foi = 'pm'
file_list = []
nfiles = len(fnamef)

for i in range(nfiles):
    """ select files of interest """
    if foi in fnamef[i]:
        file_list.append(fnamef[i])
file_list



""" NMR data processing  """
i=1
fn = file_list[0]

""" set parameters for data processing """

# todo -- create pulldown menu with parameter choices for lb_hz and solvent

dicproc ={}

# set line broadening
dicproc['lb'] = 2.0

# set solvent 
dicproc['solvent'] = 'DMAC'

""" function to process .dx files """
def proc_dx(fn, dicproc): 
    fn1=os.path.join(path, fn)
    dic, data, udic = read_dx_60MHz(fn1)

    xax = get_xax(data, udic)
    tdata = copy.deepcopy(data)


    """ window function multiplication """
    npt=tdata.shape[0]
    xpt=np.linspace(1,npt,npt)
    win = 0.5 + 0.5*np.cos(np.pi*xpt/npt)
    tdata = tdata*win

    """ line broadenting """
    lb_hz = dicproc['lb']
    lb_pts=lb_hz/udic[0]['sw']
    tdata = ng.proc_base.em(tdata, lb_pts)

    """ fft """
    tdata = ng.proc_base.fft(data) 

    """ auto phase  """
    tdata = ng.proc_autophase.autops(tdata, "peak_minima") 

    """ baseline correction """
    # auto baseline detection -- parameters altered for benchtop NMR
    xbsl, ybsl = abd(tdata, 128, 3, 60)
    tdata = bsl_poly(tdata, xbsl, ybsl, 1)
    
    """ fix x axis """
    scf = np.max(tdata.real)
    tdata = tdata/scf
    # set peak pick threshold to 0.9 for tallest peaks
    th = 0.9
    peaks = ng.peakpick.pick(tdata, th)
    xpeak = peaks['X_AXIS'].astype('int')
    
    # todo -- set up for other solvents
    if dicproc['solvent'] == 'DMAC':
        ref_ppm = 1.97
        
    # todo -- make sure solvent reference peak is first in list
    delx = xax[xpeak[0]] - ref_ppm
    xax = xax - delx
    
    return xax, tdata

xax, tdata = proc_dx(fn, dicproc)
# # plot
# fig, ax = plt.subplots()
# plt.plot(xax, tdata.real)
# plt.plot(xax, tdata.real*40 +.1)
# plt.ylim(-.01, 1.1)
# plt.gca().invert_xaxis()
# plt.title(fn)

# i=1
# fn = file_list[2]
# fn

# fn1=os.path.join(path, fn)
# dic, data = ng.fileio.jcampdx.read(fn1)
# udic = ng.fileio.jcampdx.guess_udic(dic, data)

# data.shape