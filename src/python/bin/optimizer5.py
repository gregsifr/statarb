#!/usr/bin/env python

import sys
import numpy
import math
import openopt

import util

max_sumnot = 50.0e6
max_expnot = 0.048
max_trdnot = 1.0
max_iter = 500
#min_iter = 300
#stop_iter = 200
#stop_frac = 0.001

hard_limit = 1.02
kappa = 4.3e-5
slipConst = 0.0001
#slipCoef = 2.9e4
#HAND-TWEAKED PARAMETERS TO MATCH CURRENT TRADING BEHAVIOR
slipCoef = 1.5e-2
slipExp = 0.5
#FITTED PARAMETERS
#slipCoef = 4e-4
#slipExp = 0.09
execFee= 0.0006

num_secs = 0
num_factors = 0
stocks_ii = 0
factors_ii = 0

sec_ind = dict()
sec_ind_rev = dict()
factor_ind = dict()

positions = None
lbound = None
ubound = None
mu = None
rvar = None
advp = None
borrowRate = None
price = None
factors = None
fcov = None
numpy.set_printoptions(threshold=float('nan'))

p=None
    
#def iterfn(p):
#    # because openalg seemed to ignore maxiter randomly sometimes
#    if p.iter == 0:
#        p.user.fk = []
#    else:
#        p.user.fk.append(p.fk)
#    if p.iter >= p.user.config['min_iter']:
#        if p.fk/p.user.fk[p.iter - int(p.user.config['stop_iter']) - 1] - 1.0 < p.user.config['stop_frac']:
#            return True
#        if p.iter >= p.user.config['max_iter']:
#            return True
#    return False

def printinfo(target):
    long=0
    short=0
    diff=0
    for ii in xrange(len(target)):
        if target[ii]>=0:
            long+=target[ii]
        else:
            short-=target[ii]
        diff+=abs(target[ii]-positions[ii])
    print "Long: {:.0f}, Short: {:.0f}, Total: {:.0f}".format(long,short,long+short)
    print "Dollars traded: {:.0f}".format(diff)
    __printpointinfo("Current",positions)
    __printpointinfo("Optimum",target)

def __printpointinfo(name,target):
    loadings = numpy.dot(factors, target)
    utility1 = numpy.dot(target, mu)
    utility2 = kappa * ( numpy.dot(target * rvar, target) + numpy.dot(numpy.dot(loadings, fcov), loadings) )
    utility3 = slippageFuncAdv(target, positions, advp, slipConst, slipCoef)
    utility4 = costsFunc(target, positions, borrowRate, price, execFee)
    print "@{}: mu={:.0f}, risk={:.0f}, slip={:.0f}, costs={:.2f}, ratio={:.3f}".format(name,utility1,utility2,utility3,utility4,utility1/utility2)

def slippageFuncAdv(target, positions, advp, slipConst, slipCoef):
    newpos_abs = abs(target-positions)
    slip = slipCoef * ((newpos_abs / advp)**slipExp) * newpos_abs
    return slip.sum()

def slippageFunc_grad(target, positions, advp, slipConst, slipCoef):
    newpos = target-positions
    return slipCoef * ((abs(newpos)/advp)**slipExp) * numpy.sign(newpos)

def costsFunc(target, positions, brate, price, execFee):
    costs = execFee * numpy.dot(1.0/price, abs(target - positions))
    #costs = execFee * abs(target-positions).sum()  
    #ATTENTION! borrow costs are negative, negative times negative gives a positive cost
    costs += numpy.dot(brate, numpy.minimum(0.0, target))
    return costs

def costsFunc_grad(target, positions, brate, price, execFee):
    grad = execFee * numpy.sign(target - positions) / price
    for i in xrange(len(grad)):
        #ATTENTION!  borrow costs are negative, derivative is negative (more positive position, lower costs)
        if target[i] <=0 : grad[i] += brate[i]
    return grad

def objective(target, kappa, slipConst, slipCoef, positions, mu, rvar, factors, fcov, advp, brate, price, execFee):
    # objective function to be minimized (negative utility)    
    loadings = numpy.dot(factors, target)
    #path = target - positions

    utility = 0
    utility += numpy.dot(target, mu)
    utility -= kappa * ( numpy.dot(target * rvar, target) + numpy.dot(numpy.dot(loadings, fcov), loadings) )
    utility -= slippageFuncAdv(target, positions, advp, slipConst, slipCoef)
    utility -= costsFunc(target, positions, brate, price, execFee)

    return utility

def objective_grad(target, kappa, slipConst, slipCoef, positions, mu, rvar, factors, fcov, advp, brate, price, execFee):
    F = factors
    Ft = numpy.transpose(F)
    grad = numpy.zeros(len(target))
    grad += mu
    grad -= 2 * kappa * (rvar * target + numpy.dot(Ft, numpy.dot(fcov, numpy.dot(F, target))))
    grad -= slippageFunc_grad(target,positions,advp,slipConst,slipCoef)
    grad -= costsFunc_grad(target, positions, brate, price, execFee)
    return grad

# constrain <= 0
def constrain_by_capital(target, positions, max_sumnot, factors, lbexp, ubexp, max_trdnot_hard):
    ret = abs(target).sum() - max_sumnot
    return ret

def constrain_by_capital_grad(target, positions, max_sumnot, factors, lbexp, ubexp, max_trdnot_hard):
    return numpy.sign(target)

def constrain_by_exposures(target, positions, max_sumnot, factors, lbexp, ubexp, max_trdnot_hard):
    exposures = numpy.dot(factors, target)
    ret = max(numpy.r_[lbexp - exposures, exposures - ubexp])
    return ret

def constrain_by_trdnot(target, positions, max_sumnot, factors, lbexp, ubexp, max_trdnot_hard):
    ret = abs(target - positions).sum() - max_trdnot_hard
    return ret

def optimize(daily):
    global max_sumnot, p
    
    exposures = numpy.dot(factors, positions)
    lbexp = exposures
    lbexp = numpy.minimum(lbexp, -max_expnot * max_sumnot)
    lbexp = numpy.maximum(lbexp, -max_expnot * max_sumnot * hard_limit)
    ubexp = exposures
    ubexp = numpy.maximum(ubexp, max_expnot * max_sumnot)
    ubexp = numpy.minimum(ubexp, max_expnot * max_sumnot * hard_limit)

    sumnot = abs(positions).sum()
    max_sumnot = max(max_sumnot, sumnot)
    max_sumnot = min(max_sumnot, max_sumnot * hard_limit)

        #exposure constraints
    Ac = numpy.zeros((2 * num_factors, num_secs))
    bc = numpy.zeros(2 * num_factors)
    for i in xrange(num_factors):
        for j in xrange(num_secs):
            Ac[i, j] = factors[i, j]
            Ac[num_factors + i, j] = -factors[i, j]
        bc[i] = ubexp[i]
        bc[num_factors + i] = -lbexp[i]

    #XXX optimizer needs to move in lotsize steps
    p = openopt.NLP(goal='max', f=objective, df=objective_grad, x0=numpy.zeros(num_secs), lb=lbound, ub=ubound, A=Ac, b=bc)
    p.args.f = (kappa, slipConst, slipCoef, positions, mu, rvar, factors, fcov, advp, borrowRate, price, execFee)
    p.args.df = (kappa, slipConst, slipCoef, positions, mu, rvar, factors, fcov, advp, borrowRate, price, execFee)
    p.c = [constrain_by_capital]
    p.dc = [constrain_by_capital_grad]
    p.args.c = (positions, max_sumnot, factors, lbexp, ubexp, max_trdnot * max_sumnot * hard_limit)
    p.args.dc = (positions, max_sumnot, factors, lbexp, ubexp, max_trdnot * max_sumnot * hard_limit)
    p.ftol = 1e-7
    p.maxIter = max_iter
    p.minIter = 400

    r = p.solve('ralg')
    
    #XXX need to check for small number of iterations!!!
    if r.stopcase == -1 or r.isFeasible == False:
        raise Exception("Optimization failed")

    target = r.xf
    dutil = numpy.zeros(len(target))
    dutil2 = numpy.zeros(len(target))
    dmu = numpy.zeros(len(target))
    eslip = numpy.zeros(len(target))
    costs = numpy.zeros(len(target))
    for ii in range(len(target)):
        targetwo = target.copy()
        targetwo[ii] = positions[ii]
        dutil[ii] = objective(target, *p.args.f) - objective(targetwo, *p.args.f)
        trade = target[ii]-positions[ii]
        eslip[ii] = slippageFuncAdv(target[ii], positions[ii], advp[ii], slipConst, slipCoef)
        costs[ii] = costsFunc(target, positions, borrowRate, price, execFee) - costsFunc(targetwo, positions, borrowRate, price, execFee)      
        dmu[ii] = mu[ii] * trade

        positions2 = positions.copy()
        positions2[ii] = target[ii]
        dutil2[ii] = objective(positions2, *p.args.f) - objective(positions, *p.args.f)

    printinfo(target)

    return (target, dutil, eslip, dmu, costs, dutil2)

def init():
    global num_secs, num_factors, positions, lbound, ubound, mu, rvar, advp, borrowRate, price, factors, fcov, max_iter, slipConst, slipCoef, kappa, max_sumnot, max_expnot, max_trdnot
    
    max_iter = int(config["max_iter"])
    kappa = float(config["kappa"])
    #slipConst = float(config["slipConst"])
    #slipCoef = float(config["slipCoef"])
    max_sumnot = float(config["max_sumnot"])
    max_expnot = float(config["max_expnot"])
    max_trdnot = float(config["max_trdnot"])
    
    num_secs = int(config["num_secs"])
    num_factors = int(config["num_factors"])
    
    positions = numpy.zeros(num_secs)
    lbound = numpy.zeros(num_secs) 
    ubound = numpy.zeros(num_secs)
    mu = numpy.zeros(num_secs)
    rvar = numpy.zeros(num_secs)
    advp = numpy.zeros(num_secs)
    borrowRate = numpy.zeros(num_secs)
    price = numpy.zeros(num_secs)
    factors = numpy.zeros((num_factors, num_secs))
    fcov = numpy.zeros((num_factors, num_factors)) 
    return

def getfield(field):
    try:
        ret = float(field)
        if math.isnan(ret):
            return 0.0
        return ret
    except:
        return 0.0
 
def getfield2(field):
    try:
        ret = float(field)
        if math.isnan(ret):
            return 1.0
        return ret
    except:
        return 1.0 
    
    
def processStock(fields):
    global stocks_ii, sec_ind, positions, lbound, ubound, my, rvar, advp, borrowRate, price
    sec_ind[fields[1]] = stocks_ii
    sec_ind_rev[stocks_ii] = fields[1];
    positions[stocks_ii] = getfield(fields[2])
    lbound[stocks_ii] = getfield(fields[3])
    ubound[stocks_ii] = getfield(fields[4])
    mu[stocks_ii] = getfield(fields[5])
    rvar[stocks_ii] = getfield(fields[6]) 
    advp[stocks_ii] = getfield2(fields[7])
    borrowRate[stocks_ii] = getfield(fields[8])
    price[stocks_ii] = getfield(fields[9])    
        
    stocks_ii += 1
    return
        
def processFactor(fields):
    global factors, factor_ind, factors_ii
    factor = fields[2]
    if factor not in factor_ind:
        factor_ind[factor] = factors_ii
        factors_ii += 1
        
    sec = fields[1]
    if sec in sec_ind:
        factors[factor_ind[factor], sec_ind[fields[1]]] = getfield(fields[3])
    return

def processCov(fields):
    global fcov, factor_ind
    fcov[factor_ind[fields[1]], factor_ind[fields[2]]] = getfield(fields[3])
    fcov[factor_ind[fields[2]], factor_ind[fields[1]]] = getfield(fields[3])
    return

if __name__ == "__main__":
    print("Optimizing...")
    config = dict()
    for arg in sys.argv:
        (k,d,v) = arg.partition(":")
        print "param: ", k, v
        config[k] = v

    init();

    f = open(config["optfile"], 'r')
    for line in f:
        if line.startswith("S"):
            processStock(line.split("|"))
        elif line.startswith("F"):
            processFactor(line.split("|"))
        elif line.startswith("C"):
            processCov(line.split("|"))
        else:
            util.error("Unreadable line: " + line)
    f.close()
    
    daily = True
    (target, dutil, eslip, dmu, costs, dutil2) = optimize(daily)
    
    for ii in range(len(target)):
        print "T|{}|{}|{}|{}|{}|{}|{}|{}|{}".format(ii, sec_ind_rev[ii], target[ii], dutil[ii], dmu[ii], eslip[ii], costs[ii], 0.0, dutil2[ii])

    print "DONE"
    sys.exit()
    
