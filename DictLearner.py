# -*- coding: utf-8 -*-
"""
Created on Thu Aug 20 12:01:18 2015

@author: Eric Dodds

Abstract dictionary learner.
Includes gradient descent on MSE energy function as a default learning method.
"""
import numpy as np
import pickle
import matplotlib.pyplot as plt
import StimSet
from scipy import ndimage

class DictLearner(object):

    def __init__(self, data, learnrate, nunits, paramfile=None, theta=0, moving_avg_rate=0.001,
                 stimshape=None, datatype="image", batch_size=100, pca=None):
                     
        self.nunits = nunits
        self.batch_size = batch_size
        self.learnrate = learnrate
        self.paramfile = paramfile
        self.theta=theta       
        self.moving_avg_rate=moving_avg_rate
        self.initialize_stats()
        
        self._load_stims(data, datatype, stimshape, pca)
            
        self.Q = self.rand_dict()
        self.fastmode = False # if true, some stats are not updated to save time
        
    def initialize_stats(self):
        nunits = self.nunits
        self.corrmatrix_ave = np.zeros((nunits,nunits))
        self.L0hist = np.array([])
        self.L1hist = np.array([])
        self.L2hist = np.array([])
        self.L0acts = np.zeros(nunits)
        self.L1acts = np.zeros(nunits)
        self.L2acts = np.zeros(nunits)
        self.errorhist = np.array([])
        self.meanacts = np.zeros_like(self.L0acts)
        
    def _load_stims(self, data, datatype, stimshape, pca):
        if datatype == "image":
            stimshape = stimshape or (16,16)
            self.stims = StimSet.ImageSet(data, batch_size = self.batch_size, buffer=20, stimshape = stimshape)
        elif datatype == "spectro" and pca is not None:
            if stimshape == None:
                raise Exception("When using PC representations, you need to provide the shape of the original stimuli.")
            self.stims = StimSet.PCvecSet(data, stimshape, pca, self.batch_size)
        elif datatype == "waveform" and pca is not None:
            self.stims = StimSet.WaveformPCSet(data, stimshape, pca, self.batch_size)
        else:
            raise ValueError("Specified data type not currently supported.")
    
    def infer(self, data, infplot):
        raise NotImplementedError
        
    def test_inference(self, niter=None):
        temp = self.niter
        self.niter = niter or self.niter
        X = self.stims.rand_stim()
        s = self.infer(X, infplot=True)[0]
        self.niter = temp
        print("Final SNR: " + str(self.snr(X,s)))
        return s
        
    def generate_model(self, acts):
        """Reconstruct inputs using linear generative model."""
        return np.dot(self.Q.T,acts)
        
    def compute_errors(self, acts, X):
        """Given a batch of data and activities, compute the squared error between
        the generative model and the original data. Returns vector of mean squared errors."""
        diffs = X - self.generate_model(acts)
        return np.mean(diffs**2,axis=0)/np.mean(X**2,axis=0)      
        
    def smoothed_error(self, window_size=1000, start=0, end=-1):
        """Plots a moving average of the error history with the given averaging window."""
        window = np.ones(int(window_size))/float(window_size)
        smoothed = np.convolve(self.errorhist[start:end], window, 'valid')
        plt.plot(smoothed)
        
    def progress_plot(self, window_size=1000, norm=1, start=0, end=-1):
        """Plots a moving average of the error and activity history with the given averaging window."""
        window = np.ones(int(window_size))/float(window_size)
        smoothederror = np.convolve(self.errorhist[start:end], window, 'valid')
        if norm==2:
            acthist = self.L2hist
        elif norm==0:
            acthist = self.L0hist
        else:
            acthist = self.L1hist
        smoothedactivity = np.convolve(acthist[start:end], window, 'valid')
        plt.plot(smoothederror, 'b', smoothedactivity, 'g')
    
    def snr(self, data, acts):
        """Returns the signal-noise ratio for the given data and coefficients."""
        sig = np.var(data,axis=0)
        noise = np.var(data - self.Q.T.dot(acts), axis=0)
        return np.mean(sig/noise)
    
    def learn(self, data, coeffs, normalize = True):
        """Adjust dictionary elements according to gradient descent on the 
        mean-squared error energy function, optionally with an extra term to
        increase orthogonality between basis functions. This term is
        multiplied by the parameter theta.
        Returns the mean-squared error."""
        R = data.T - np.dot(coeffs.T, self.Q)
        self.Q = self.Q + self.learnrate*np.dot(coeffs,R)
        if self.theta != 0:
            # Notice this is calculated using the Q after the mse learning rule
            thetaterm = (self.Q - np.dot(self.Q,np.dot(self.Q.T,self.Q)))
            self.Q = self.Q + self.theta*thetaterm
        if normalize:
            # force dictionary elements to be normalized
            normmatrix = np.diag(1./np.sqrt(np.sum(self.Q*self.Q,1))) 
            self.Q = normmatrix.dot(self.Q)
        return np.mean(R**2)
            
    def run(self, ntrials = 1000, batch_size = None, show=False, rate_decay=None, normalize = True):
        batch_size = batch_size or self.stims.batch_size
        for trial in range(ntrials):
            if trial % 50 == 0:
                print (trial)
                
            X = self.stims.rand_stim(batch_size=batch_size)
            acts,_,_ = self.infer(X)
            thiserror = self.learn(X, acts, normalize)
            
            self.store_statistics(acts, thiserror, batch_size)
            
            if (trial % 1000 == 0 or trial+1 == ntrials) and trial != 0:
                try: 
                    print ("Saving progress to " + self.paramfile)
                    self.save()
                except (ValueError, TypeError) as er:
                    print ('Failed to save parameters. ', er)
            if rate_decay is not None:
                self.adjust_rates(rate_decay)
        if show:
            plt.figure()
            plt.plot(self.errorhist)
            plt.show()        
            
    def store_statistics(self, acts, thiserror, batch_size=None, center_corr=True):
        batch_size = batch_size or self.batch_size
        self.L2acts = (1-self.moving_avg_rate)*self.L2acts + self.moving_avg_rate*(acts**2).mean(1)
        self.L1acts = (1-self.moving_avg_rate)*self.L1acts + self.moving_avg_rate*np.abs(acts).mean(1)
        L0means = np.mean(acts != 0, axis=1)
        self.L0acts = (1-self.moving_avg_rate)*self.L0acts + self.moving_avg_rate*L0means
        means = acts.mean(1)
        self.meanacts = (1-self.moving_avg_rate)*self.meanacts + self.moving_avg_rate*means
        self.errorhist = np.append(self.errorhist, thiserror)
        self.L0hist = np.append(self.L0hist, np.mean(acts!=0))
        self.L1hist = np.append(self.L1hist, np.mean(np.abs(acts)))
        self.L2hist = np.append(self.L2hist, np.mean(acts**2))
        try:
            if self.fastmode:
                # skip computing the correlation matrix, which is relatively expensive
                return
        except:
            pass
        if center_corr:
            actdevs = acts-means[:,np.newaxis]
            corrmatrix = (actdevs).dot(actdevs.T)/batch_size
        else:
            corrmatrix = acts.dot(acts.T)/self.batch_size
        self.corrmatrix_ave = (1-self.moving_avg_rate)*self.corrmatrix_ave + self.moving_avg_rate*corrmatrix
        return corrmatrix
        
    
    def show_dict(self, stimset=None, cmap='jet', subset=None, square=False, savestr=None):
        """Plot an array of tiled dictionary elements. The 0th element is in the top right."""
        stimset = stimset or self.stims
        if subset is not None:
            indices = np.random.choice(self.Q.shape[0], subset)
            Qs = self.Q[np.sort(indices)]
        else:
            Qs = self.Q
        array = stimset.stimarray(Qs[::-1], square=square)
        plt.figure()        
        arrayplot = plt.imshow(array,interpolation='nearest', cmap=cmap, aspect='auto', origin='lower')
        plt.axis('off')
        plt.colorbar()
        if savestr is not None:
            plt.savefig(savestr, bbox_inches='tight')
        return arrayplot
        
    def show_element(self, index, cmap='jet', labels=None, savestr=None):
        elem = self.stims.stim_for_display(self.Q[index])
        plt.figure()
        plt.imshow(elem.T, interpolation='nearest',cmap=cmap, aspect='auto', origin='lower')
        if labels is None:
            plt.axis('off')
        else:
            plt.colorbar()
        if savestr is not None:
            plt.savefig(savestr, bbox_inches='tight')
               
    def rand_dict(self):
        Q = np.random.randn(self.nunits, self.stims.datasize)
        return (np.diag(1/np.sqrt(np.sum(Q**2,1)))).dot(Q)
        
    def adjust_rates(self, factor):
        """Multiply the learning rate by the given factor."""
        self.learnrate = factor*self.learnrate
        self.theta = factor*self.theta

    def modulation_plot(self, usepeaks=False, **kwargs):
        modcentroids = np.zeros((self.Q.shape[0],2))
        for ii in range(self.Q.shape[0]):
            modspec = self.stims.modspec(self.Q[ii])
            if usepeaks:
                modcentroids[ii,0] = np.argmax(np.mean(modspec,axis=1))
                modcentroids[ii,1] = np.argmax(np.mean(modspec,axis=0))
            else:
                modcentroids[ii] = ndimage.measurements.center_of_mass(modspec)
        plt.scatter(modcentroids[:,0], modcentroids[:,1])
        plt.title('Center of mass of modulation power spectrum of each dictionary element')
        try:
            plt.xlabel(kwargs.xlabel)
        except:
            pass
        try:
            plt.ylabel(kwargs.ylabel)
        except:
            pass
        
    def sort_dict(self, batch_size=None, plot = False, allstims = True, savestr=None):
        """Sorts the RFs in order by their usage on a batch. Default batch size
        is 10 times the stored batch size. Usage means 1 for each stimulus for
        which the element was used and 0 for the other stimuli, averaged over 
        stimuli."""
        if allstims:
            testX = self.stims.data.T
        else:
            batch_size = batch_size or 10*self.batch_size
            testX = self.stims.rand_stim(batch_size)
        means = np.mean(self.infer(testX)[0] != 0, axis=1)
        sorter = np.argsort(means)
        self.sort(means, sorter, plot, savestr)
        return means[sorter]
        
    def fast_sort(self, L1=False, plot=False, savestr=None):
        """Sorts RFs in order by moving average usage."""
        if L1:
            usages = self.L1acts
        else:
            usages = self.L0acts
        sorter = np.argsort(usages)
        self.sort(usages, sorter, plot, savestr)
        return usages[sorter]
    
    def sort(self, usages, sorter, plot=False, savestr=None):
        self.Q = self.Q[sorter]
        self.L0acts = self.L0acts[sorter]
        self.L1acts = self.L1acts[sorter]
        self.L2acts = self.L2acts[sorter]
        self.meanacts = self.meanacts[sorter]
        self.corrmatrix_ave = self.corrmatrix_ave[sorter, sorter]
        if plot:
            plt.figure()
            plt.plot(usages[sorter])
            plt.title('L0 Usage')
            plt.xlabel('Dictionary index')
            plt.ylabel('Fraction of stimuli')
            if savestr is not None:
                plt.savefig(savestr,format='png', bbox_inches='tight')
                
    def load(self, filename=None):
        if filename is None:
            filename = self.paramfile
        self.paramfile = filename
        with open(filename, 'rb') as f:
            self.Q, params, histories = pickle.load(f)
        (self.errorhist, self.meanacts, self.L0acts, self.L0hist,
                     self.L1acts, self.L1hist, self.L2hist, self.L2acts,
                     self.corrmatrix_ave) = histories
        self.set_params(params)
        
    def set_params(self, params):
        raise NotImplementedError
        
    def get_param_list(self):
        raise NotImplementedError
        
    def save(self, filename=None):
        filename = filename or self.paramfile
        if filename is None:
            raise ValueError("You need to input a filename.")
        self.paramfile = filename
        params = self.get_param_list()
        histories = (self.errorhist, self.meanacts, self.L0acts, self.L0hist,
                     self.L1acts, self.L1hist, self.L2hist, self.L2acts,
                     self.corrmatrix_ave)
        with open(filename, 'wb') as f:
            pickle.dump([self.Q, params, histories], f)
               
