# -*- coding: utf-8 -*-
"""
Created on Thu Aug 20 15:48:46 2015

@author: Eric Dodds
(Inference method adapted from code by Jesse Livezey)
Dictionary learner that uses LCA for inference and gradient descent for learning.
(Intended for static inputs)
"""
import numpy as np
import matplotlib.pyplot as plt
import StimSet
from DictLearner import DictLearner
import pickle
from numbapro import cuda
import numbapro.cudalib.cublas as cublas
from numba import *
from math import ceil


class LCALearner(DictLearner):
    
    def __init__(self, data, nunits, learnrate=None, theta = 0.022,
                 batch_size = 100, infrate=.003, #.0005 in Nicole's notes
                 niter=300, min_thresh=0.4, adapt=0.95, tolerance = .01, max_iter=4,
                 softthresh = False, datatype = "image",
                 pca = None, stimshape = None, paramfile = None, gpu=False):
        """
        An LCALearner is a dictionary learner (DictLearner) that uses a Locally Competitive Algorithm (LCA) for inference.
        By default the LCALearner optimizes for sparsity as measured by the L0 pseudo-norm of the activities of the units
        (i.e. the usages of the dictionary elements). 
        
        Args:
            data: data presented to LCALearner for estimating with LCA
            nunits: number of units in thresholding circuit = number dictionary elements
            learnrate: rate for mean-squared error part of learning rule
            theta: rate for orthogonality constraint part of learning rule
            batch_size: number of data presented for inference per learning step
            infrate: rate for evolving the dynamical equation in inference (size of each step)
            niter: number of steps in inference (if tolerance is small, chunks of this many iterations are repeated until tolerance is satisfied)
            min_thresh: thresholds are reduced during inference no lower than this value. sometimes called lambda, multiplies sparsity constaint in objective function
            adapt: factor by which thresholds are multipled after each inference step
            tolerance: inference ceases after mean-squared error falls below tolerance
            max_iter: maximum number of chunks of inference (each chunk having niter iterations)
            softthresh: if True, optimize for L1-sparsity
            datatype: image or spectro
            pca: pca object for inverse-transforming data if used in PC representation
            stimshape: original shape of data (e.g., before unrolling and PCA)
            paramfile: a pickle file with dictionary and error history is stored here     
            gpu: whether or not to use the GPU implementation of
        """
        self.batch_size = batch_size
        learnrate = learnrate or 1./self.batch_size
        if datatype == "image":
            stimshape = stimshape or (16,16)
            self.stims = StimSet.ImageSet(data, batch_size = self.batch_size, buffer=20, stimshape = stimshape)
        elif datatype == "spectro" and pca is not None:
            if stimshape == None:
                raise Exception("When using PC representations, you need to provide the shape of the original stimuli.")
            self.stims = StimSet.PCvecSet(data, stimshape, pca, self.batch_size)
        else:
            raise ValueError("Specified data type not currently supported.")
        self.nunits = nunits
        self.infrate = infrate
        self.niter = niter
        self.min_thresh = min_thresh
        self.adapt = adapt
        self.softthresh = softthresh
        self.tolerance = tolerance
        self.max_iter = max_iter
        self.gpu = gpu
        super().__init__(learnrate, paramfile = paramfile, theta=theta)
        
    def show_oriented_dict(self, batch_size='all', *args):
        """Display tiled dictionary as in DictLearn.show_dict(), but with elements inverted
        if their activities tend to be negative."""
        if batch_size == 'all':
            X = self.stims.data.T
        else:
            X = self.stims.random_stim(batch_size)
        means = np.mean(self.infer(X)[0],axis=1)
        toflip = means < 0
        realQ = self.Q
        self.Q[toflip] = -self.Q[toflip]
        result = self.show_dict(args)
        self.Q = realQ
        return result
    
    def infer_cpu(self, X, infplots=False, tolerance=None, max_iter = None):
        """Infer sparse approximation to given data X using this LCALearner's 
        current dictionary. Returns coefficients of sparse approximation.
        Optionally plot reconstruction error vs iteration number.
        The instance variable niter determines for how many iterations to evaluate
        the dynamical equations. Repeat this many iterations until the mean-squared error
        is less than the given tolerance."""
        tolerance = tolerance or self.tolerance
        max_iter = max_iter or self.max_iter
        
        ndict = self.Q.shape[0]
        nstim = X.shape[-1]
        u = np.zeros((nstim, ndict))
        s = np.zeros_like(u)
        ci = np.zeros_like(u)
        
        # c is the overlap of dictionary elements with each other, minus identity (i.e., ignore self-overlap)
        c = self.Q.dot(self.Q.T) - np.eye(ndict)
        
        # b[i,j] is overlap of stimulus i with dictionary element j
        b = (self.Q.dot(X)).T

        # initialize threshold values, one for each stimulus, based on average response magnitude
        thresh = np.absolute(b).mean(1) 
        thresh = np.array([np.max([thr, self.min_thresh]) for thr in thresh])
        
        if infplots:
            errors = np.zeros((self.niter, nstim))
            histories = np.zeros((ndict, self.niter))
            shistories = np.zeros((ndict, self.niter))
            threshhist = np.zeros((nstim, self.niter))
        
        error = tolerance+1
        outer_k = 0
        while(error>tolerance and ((max_iter is None) or outer_k<max_iter)):
            for kk in range(self.niter):
                # ci is the competition term in the dynamical equation
                ci[:] = s.dot(c)
                u[:] = self.infrate*(b-ci) + (1.-self.infrate)*u
                if np.max(np.isnan(u)):
                    raise ValueError("Internal variable blew up at iteration " + str(kk))
                if self.softthresh:
                    s[:] = np.sign(u)*np.maximum(0.,np.absolute(u)-thresh[:,np.newaxis]) 
                else:
                    s[:] = u
                    s[np.absolute(s) < thresh[:,np.newaxis]] = 0
                    
                if infplots:
                    histories[:,kk] = u[0,:]
                    shistories[:,kk] = s[0,:]
                    errors[kk,:] = np.mean((X.T - s.dot(self.Q))**2,axis=1)
                    threshhist[:,kk] = thresh
                    
                thresh = self.adapt*thresh
                thresh[thresh<self.min_thresh] = self.min_thresh
                
            error = np.mean((X.T - s.dot(self.Q))**2)
            outer_k = outer_k+1
        
        if infplots:
            plt.figure(3)
            plt.clf()
            plt.plot(errors)
            plt.figure(4)
            plt.clf()
            hists = np.concatenate((histories,shistories),axis=0)
            plt.plot(hists.T)
            return s.T, errors, histories, shistories, threshhist
        return s.T, u.T, thresh
            
    def test_inference(self, niter=None):
        temp = self.niter
        self.niter = niter or self.niter
        X = self.stims.rand_stim()
        s = self.infer(X, infplots=True)
        self.niter = temp
        return s
                  
    def sort_dict(self, batch_size=None, plot = False, allstims = True):
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
        self.Q = self.Q[sorter]
        if plot:
            plt.plot(means[sorter])
        return means[sorter]
        
    def adjust_rates(self, factor):
        """Multiply the learning rate by the given factor."""
        self.learnrate = factor*self.learnrate
        #self.infrate = self.infrate*factor # TODO: this is bad, temporary
        
    def load_params(self, filename=None):
        self.paramfile = filename
        try:
            with open(filename, 'rb') as f:
                self.Q, params, self.errorhist = pickle.load(f)
            self.learnrate, self.theta, self.min_thresh, self.infrate, self.niter, self.adapt, self.max_iter, self.tolerance = params
        except ValueError:
            # this is so I can still load older files from when I saved less stuff
            with open(filename, 'rb') as f:
                self.Q, self.errorhist = pickle.load(f)
        self.picklefile = filename
        
    def save_params(self, filename=None, dialog=False):
        filename = filename or self.paramfile
        if filename is None:
            raise ValueError("You need to input a filename.")
        self.paramfile = filename        
        params = (self.learnrate, self.theta, self.min_thresh, self.infrate, self.niter, self.adapt, self.max_iter, self.tolerance)
        with open(filename, 'wb') as f:
            pickle.dump([self.Q, params, self.errorhist], f)
        
    ### GPU implementation by Jesse Livezey, adapted by EMD
        
    @cuda.jit('void(f4[:,:])')
    def csub(c):
        n = c.shape[0]
        i = cuda.grid(1)
        
        if i<n:
            c[i,i] = 0.

    @cuda.jit('void(f4[:,:],f4[:,:],f4[:,:],f4[:,:],f4[:,:],f4,f4[:],f4,f4,i4)')
    def iterate(c,b,ci,u,s,eta,thresh,lamb,adapt,softThresh):
        n = u.shape[0]
        m = u.shape[1]
        i,j = cuda.grid(2)
        
        if i<n and j< m:
            u[i,j] = eta*(b[i,j]-ci[i,j])+(1-eta)*u[i,j]
            if u[i,j] < thresh[i] and u[i,j] > -thresh[i]:
                s[i,j] = 0.
            elif softThresh == 1:
                if u[i,j] > 0.:
                    s[i,j] = u[i,j]-thresh[i]
                else:
                    s[i,j] = u[i,j]+thresh[i]
            else:
                s[i,j] = u[i,j]
            thresh[i] = thresh[i]*adapt
            if thresh[i] < lamb:
                thresh[i] = lamb
    
    def infer_gpu(self, stimuli, coeffs=None):
    #Get Blas routines
        blas = cublas.Blas()
    #Initialize arrays
        numDict = self.Q.shape[0]
        numStim = stimuli.shape[0]
        dataLength = stimuli.shape[1]
        u = np.zeros((numStim, numDict), dtype=np.float32, order='F')
        if coeffs is not None:
            u[:] = np.atleast_2d(coeffs)
        d_u = cuda.to_device(u)
        d_s = cuda.to_device(np.zeros((numStim,numDict),dtype=np.float32,order='F'))
        d_b = cuda.to_device(np.zeros((numStim,numDict),dtype=np.float32,order='F'))
        d_ci = cuda.to_device(np.zeros((numStim,numDict),dtype=np.float32,order='F'))
        d_c = cuda.to_device(np.zeros((numDict,numDict),dtype=np.float32,order='F'))
        
        #Move inputs to GPU
        d_dictionary = cuda.to_device(np.array(self.Q,dtype=np.float32,order='F'))
        d_stimuli = cuda.to_device(np.array(stimuli,dtype=np.float32,order='F'))
    
        blockdim2 = (32,32) # TODO: experiment, was all 32s
        blockdim1 = 32
        griddimcsub = int(ceil(numDict/blockdim1))
        griddimi = (int(ceil(numStim/blockdim2[0])),int(ceil(numDict/blockdim2[1])))
        
        #Calculate c: overlap of basis functions with each other minus identity
        blas.gemm('N','T',numDict,numDict,dataLength,1.,d_dictionary,d_dictionary,0.,d_c)
        LCALearner.csub[griddimcsub,blockdim1](d_c)
        blas.gemm('N','T',numStim,numDict,dataLength,1.,d_stimuli,d_dictionary,0.,d_b)
        thresh = np.mean(np.absolute(d_b.copy_to_host()),axis=1)
        d_thresh = cuda.to_device(thresh)
        #Update u[i] and s[i] for niter time steps
        for kk in range(self.niter):
            #Calculate ci: amount other neurons are stimulated times overlap with rest of basis
            blas.gemm('N','N',numStim,numDict,numDict,1.,d_s,d_c,0.,d_ci)
            LCALearner.iterate[griddimi,blockdim2](d_c,d_b,d_ci,d_u,d_s,self.infrate,d_thresh,self.min_thresh,self.adapt,self.softthresh)
        u = d_u.copy_to_host()
        s = d_s.copy_to_host()
        return s.T,u.T,thresh
        
    def infer(self, X, infplots=False, tolerance=None, max_iter = None):
        if self.gpu:
            # right now there is no support for multiple blocks of iterations, stopping after error crosses threshold, or plots monitoring inference
            return self.infer_gpu(X.T)
        else:
            return self.infer_cpu(X, infplots, tolerance, max_iter)