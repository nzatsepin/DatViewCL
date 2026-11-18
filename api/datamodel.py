# datview/api/datamodel.py
# Main api of program. DataModel contains the data and filters, and manages all load and save options. 
# Author Natasha Stander
# 2026 update: lower memory loader (no duplicate raw table), lazy selection copy,
# binary stream export and direct merging via api.streamio.

try:
    from PyQt5.QtCore import QObject,pyqtSignal
except ImportError:
    from PyQt4.QtCore import QObject,pyqtSignal

import numpy as np
from .filters import *
from .groupmgr import GroupMgr
import lxml.etree as ElementTree
from .modelcfg import ModelConfig
from . import streamio
import sys

try:
    import pandas as pd
except ImportError:
    pd = None


class DataModel(QObject):
    filterchange=pyqtSignal()
    sortchange=pyqtSignal()
    filterModelChange=pyqtSignal(GroupFilter)
    stackChange=pyqtSignal()
    sortColumnName="datview_sort_order"
    compareGroupName="datview_compare_group"
    compareIndexName="datview_compare_row"
    genColSeparator="*"
    cmpColTemplate="%s*cmp*%i*%i" # arguments field, compare group 1, compare group 2

    def __init__(self,filename,groupfile=None,cfg=None):
        QObject.__init__(self)
        if cfg is None:
            self.cfg = ModelConfig()
        else:
            self.cfg = cfg
        self.cols=[]
        self.hdrline=None
        self.digitized={}
        self.rawcols={}   # original values of columns changed on load (mult/invert), for dat export
        self.rawfmt={}    # string columns: output width not needed, kept for completeness
        self.data = None
        self.npfile = None
        self.cmparray=None
        self._filtered=None
        self._keepcount=None
        
        if filename.endswith(".npz"):
            self.loadNumpy(filename)
        else:
            self.loadtxt(filename)

        self.rootfilter=AndFilter(self.data.shape) # Includes filters, partitions, flags
        self.topfilter=AndFilter(self.data.shape) # Top of filters Tree
        self.rootfilter.addChild(self.topfilter)
        self.partitionfilter=DataFilter(np.ones(self.data.shape,dtype=bool))
        self.rootfilter.addChild(self.partitionfilter)
        self.flagFilter=DataFilter(np.zeros(self.data.shape,dtype=bool))
        self.flagFilter.setActive(False)
        self.rootfilter.addChild(self.flagFilter)
        self.rootfilter.filterchange.connect(self.applyFilters)
        self.internalFilterChanges=False

        self.mincache={}
        self.maxcache={}
        self.tmincache={}
        self.labelcache={}
        self.groupmgr=None
        if groupfile:
            self.groupmgr=GroupMgr(groupfile)
        self.selfilters={}
        self.sortlst=[]
        self.reverseSort=False
        self.limit=None
        self.limitModeRandom = True
        self.stacks=None


    def loadtxt(self,filename):
        """Called from __init__, not intended to be called by users.

        String columns (file paths, indexing method, ...) are stored once, as integer codes
        into a sorted label array, instead of as fixed-width unicode in a second copy of the
        table. Only columns altered on load (mult/invert in the config) keep their original
        values, so a re-exported dat file is identical to the input.
        """
        with open(filename) as dfile:
            self.hdrline=dfile.readline().strip()
        if self.hdrline.startswith("CrystFEL stream format"):
            print("Usage: This input file (%s) looks like a CrystFEL stream file. It should be a dat file. Please run \n\tdatgen.py -o output.dat %s\nand run this program on the output."%(filename,filename),file=sys.stderr)
            sys.exit()
        self.cols=self.hdrline.split(self.cfg.sep)
        if self.cfg.commentchar is not None:
            self.cols[0] = self.cols[0].replace(self.cfg.commentchar, "").strip()
            if self.cols[0] == "":
                del self.cols[0]
        self.hdrline=(self.cfg.sep or "\t").join(self.cols)

        self.strcols={c for c in self.cols if 'U' in self.cfg.dtype(c)}
        columns=self._readColumns(filename)

        n=len(columns[self.cols[0]]) if self.cols else 0
        dtypes2=[]
        for c in self.cols:
            dtypes2.append('i4' if c in self.strcols else self.cfg.dtype(c))
        self.cols.append(DataModel.sortColumnName)
        dtypes2.append('u4')
        self.data=np.empty(n,dtype={'names':tuple(self.cols),'formats':tuple(dtypes2)})
        for c in self.cols:
            if c == DataModel.sortColumnName:
                self.data[c]=np.arange(n)
            elif c in self.strcols:
                lbls,inverse=np.unique(columns[c].astype(str),return_inverse=True)
                self.digitized[c]=np.array(lbls.tolist())
                self.data[c]=inverse.ravel()
            else:
                raw=columns[c]
                converted = c in self.cfg.invert or self.cfg.multvalue(c) != 1
                if converted:
                    self.rawcols[c]=raw
                self.data[c]=raw
                if c in self.cfg.invert:
                    with np.errstate(divide='ignore'):
                        self.data[c] = 1/self.data[c]
                if self.cfg.multvalue(c) != 1:
                    self.data[c][self.data[c]!=-1]*=self.cfg.multvalue(c)
            columns[c]=None # release the parsed text column as soon as it is copied

    def _readColumns(self,filename):
        """Parse the table body into a dict of 1D arrays, one per column.

        Uses pandas' C parser when available (several times faster than numpy.loadtxt on
        large tables); falls back to numpy otherwise. Trailing separators written by older
        datgen versions are tolerated.
        """
        strcols=sorted(self.strcols)
        if pd is not None:
            sep=self.cfg.sep if self.cfg.sep is not None else r"\s+"
            df=pd.read_csv(filename,sep=sep,header=None,skiprows=1,names=self.cols,
                           usecols=range(len(self.cols)),index_col=False,
                           dtype={c:str for c in strcols},engine="c")
            out={}
            for c in self.cols:
                col=df[c]
                if c not in strcols and col.dtype == object:
                    # Text in a column not declared as a string in the config (e.g. a plate
                    # well label from a positions table): treat it as categorical.
                    self.strcols.add(c)
                if c in self.strcols:
                    out[c]=col.astype(str).to_numpy(dtype=str) if col.dtype != object else col.fillna("").to_numpy(dtype=str)
                else:
                    out[c]=self._numeric(col.to_numpy(),self.cfg.dtype(c))
                del df[c]
            return out
        dtypes=[]
        for c in self.cols:
            dtypes.append(self.cfg.dtype(c) if 'U' not in self.cfg.dtype(c) else 'U1024')
        arr=np.loadtxt(filename,skiprows=1,delimiter=self.cfg.sep,usecols=range(len(self.cols)),ndmin=1,
                       dtype={'names':tuple(self.cols),'formats':tuple(dtypes)})
        return {c:arr[c] for c in self.cols}

    @staticmethod
    def _numeric(values,dtype):
        try:
            return values.astype(dtype)
        except (ValueError,TypeError):
            return np.asarray(values,dtype=float).astype(dtype)

    def loadNumpy(self,filename):
        self.npfile = np.load(filename)
        self.data=self.npfile["data"]
        self.cols=list(self.data.dtype.names)
        for dkey in self.npfile["digitizedkeys"].tolist():
            self.digitized[dkey]=None
        if "cmparray" in self.npfile.keys():
            self.cmparray=self.npfile["cmparray"]
        if "rawcolkeys" in self.npfile.keys():
            for c in self.npfile["rawcolkeys"].tolist():
                self.rawcols[c]=self.npfile["raw__"+c]
        if "hdrline" in self.npfile.keys():
            self.hdrline=str(self.npfile["hdrline"])
        

    def prettyname(self,field):
        origfield=field
        field=self.datafield(field)
        if DataModel.genColSeparator in origfield:
            parts=origfield.split(DataModel.genColSeparator)
            assert parts[1] == "cmp" # Only one that should appear right now
            group1=int(parts[2])
            return "%s of %s" %(self.cfg.prettyname(field), self.stringValue(DataModel.compareGroupName,group1))
        return self.cfg.prettyname(field)

#########################################
###           Categorical             ###
#########################################

    def hasLabels(self,field):
        field=self.datafield(field)
        return self.groupmgr is not None and field.startswith(GroupMgr.prefix) or field in self.digitized

    def canAccessDigitized(self,field):
        """ Lazy load digitized values from numpy file """
        if field in self.digitized:
            if self.digitized[field] is None:
                assert self.npfile is not None
                self.digitized[field]=self.npfile[field]
            return True
        return False

    def labels(self,field):
        field=self.datafield(field)
        if self.hasLabels(field):
            if field not in self.labelcache:
                self.labelcache[field]={}
                if self.canAccessDigitized(field):
                    self.labelcache[field]["labels"] = self.digitized[field]
                    self.labelcache[field]["ints"] = np.arange(len(self.digitized[field]))
                else:
                    self.labelcache[field]["ints"] = np.unique(self.data[field])
                    labels=[]
                    for i in self.labelcache[field]["ints"]:
                        labels.append(self.groupmgr.value(field[len(GroupMgr.prefix):],i))
                    self.labelcache[field]["labels"] = labels
            return self.labelcache[field]["labels"]
        elif self.isCategorical(field):
            # If it is categorical, but doesn't have labels, then it has numerical values and 
            # the unique values present should be returned. This covers cases like Run or # of 
            # crystals on a frame.
            if field not in self.labelcache:
                self.labelcache[field]={}
                self.labelcache[field]["labels"]=np.unique(self.data[field])
                self.labelcache[field]["ints"]=self.labelcache[field]["labels"]
            return self.labelcache[field]["labels"]
        return []

    def labelints(self,field):
        field=self.datafield(field)
        if self.isCategorical(field):
            if field not in self.labelcache:
                self.labels(field)
            return self.labelcache[field]["ints"]
        return []
        

    def isCategorical(self,field):
        """Return true if values in field are categorical or discrete. Basically flags what should be
           a bar chart versus histogram. Groups are always categorical, columns stored as strings are
           always categorical and some columns with known limited values are specified categorical such
           as multi """
        return field.startswith(GroupMgr.prefix) or field in self.digitized or field in self.cfg.categorical

    def intValues(self,field):
        """Meant for categorical values, return list of all possible"""
        field=self.datafield(field)
        if self.canAccessDigitized(field):
            return range(len(self.digitized[field])) # It's digitized 0:N-1
        elif self.groupmgr is not None and field.startswith(GroupMgr.prefix):
            return range(len(self.groupmgr.values(field[len(GroupMgr.prefix):]))) # Valid groupfiles are 0:N-1 
        else:
            vals = np.unique(self.data[field])
            r=[]
            for v in vals:
                r.append(int(v))
            return r

    def value(self,field,i,filtered=True):
        """Return the value of the field at (filtered if filtered=True) row i. Returns true value rather than digitized or group-id value"""
        field=self.datafield(field)

        if filtered:
            v=self.filtered[field][i]
        else:
            v=self.data[field][i]
        return self.stringValue(field,v)

    def stringValue(self,field,v):
        field=self.datafield(field)

        if self.canAccessDigitized(field):
            if v >= 0 and v < len(self.digitized[field]):
                v = self.digitized[field][int(v)]
            else:
                v=""
        elif field.startswith(GroupMgr.prefix) and self.groupmgr is not None:
            v = self.groupmgr.value(field[len(GroupMgr.prefix):],v)
        return str(v)

    def intValue(self,field,i):
        field=self.datafield(field)

        r=None
        if self.canAccessDigitized(field):
            r=np.where(self.digitized[field] == i)[0][0]
        elif field.startswith(GroupMgr.prefix) and self.groupmgr is not None:
            r = self.groupmgr.gid(field[len(GroupMgr.prefix):],i)
        if r is None:
            try:
                r=int(i)
            except ValueError:
                r=None
                pass
        return r

    def datafield(self,field):
        """Return the field name as it appears in the structured array"""
        if field is not None and field not in self.cols: 
            if DataModel.genColSeparator in field:
                field=field.split(DataModel.genColSeparator)[0]
            if (GroupMgr.prefix+field) in self.cols:
                field=GroupMgr.prefix+field
            elif field[len(GroupMgr.prefix):] in self.cols:
                field = field[len(GroupMgr.prefix):]
        return field

#########################################
###            Filtering              ###
#########################################                

    @property
    def filtered(self):
        """Selected rows as a structured array. Built on first use after each selection
        change; plots do not use it, so dragging a selection no longer copies the table."""
        if self._filtered is None:
            self._filtered=self.data[self.rootfilter.getKeep()]
        return self._filtered

    def selectedCount(self):
        if self._keepcount is None:
            self._keepcount=int(np.count_nonzero(self.rootfilter.getKeep()))
        return self._keepcount

    def isFiltered(self):
        return self.selectedCount() < len(self.data)

    def addFilter(self,toAdd):
        self.topfilter.addChild(toAdd)

    def selectionFilter(self,field):
        origfield=field
        field=self.datafield(field)
        if field not in self.selfilters and origfield not in self.selfilters:
            if DataModel.genColSeparator in origfield:
                parts=origfield.split(DataModel.genColSeparator)
                assert parts[1] == "cmp" # Only one that should appear right now
                group1=int(parts[2])
                group2=int(parts[3])

                f1=BetweenFilter(self.fieldmin(field)-1,self.fieldmax(field)+1,self.cmpvalues(field)[:,group1],field)
                f2=BetweenFilter(self.fieldmin(field)-1,self.fieldmax(field)+1,self.cmpvalues(field)[:,group2],field)
                f1.setActive(False)
                f2.setActive(False)
                nm="%s,%s"%(self.stringValue(DataModel.compareGroupName,group1),self.stringValue(DataModel.compareGroupName,group2))
                f=PairBetweenFilter(self.data.shape,self.cmparray,f1,f2,group1,group2,nm)
                self.addFilter(f)
                self.selfilters[origfield]=f1
                self.selfilters[DataModel.cmpColTemplate%(parts[0],group2,group1)]=f2
            else:
                f=BetweenFilter(self.fieldmin(field)-1,self.fieldmax(field)+1,self.data[field],field)
                f.setActive(False)
                self.addFilter(f)
                self.selfilters[field]=f
        if origfield in self.selfilters:
            return self.selfilters[origfield]
        return self.selfilters[field]

    def applyFilters(self):
        self._filtered=None
        self._keepcount=None
        if not self.internalFilterChanges:
            self.filterchange.emit()

    def fieldmin(self,field):
        field=self.datafield(field)
        if field not in self.mincache:
            valid = self.data[field]
            valid = valid[valid != -1]
            if len(valid):
                self.mincache[field]=np.min(valid)
            else:
                self.mincache[field]=-1
        return self.mincache[field]

    def trueFieldMin(self,field):
        field=self.datafield(field)
        if field not in self.tmincache:
            self.tmincache[field]=np.min(self.data[field])
        return self.tmincache[field]

    def fieldmax(self,field):
        field=self.datafield(field)
        if field not in self.maxcache:
            self.maxcache[field]=np.max(self.data[field])
        return self.maxcache[field]

#########################################
###            Partition              ###
#########################################  

    def partitionMulti(self,fields,minimums=[],maximums=[],nums=[]):
        l=len(fields)
        minimums+=[None]*(l-len(minimums))
        maximums+=[None]*(l-len(maximums))
        nums+=[None]*(l-len(nums))
            
        cur=None
        next=None
        for i,field in enumerate(fields):
            if cur is None:
                cur=self.partition(field,minimums[i],maximums[i],nums[i])
            else:
                next=self.partition(field,minimums[i],maximums[i],nums[i])
                tmpcur=cur
                cur={}
                for n1,v1 in tmpcur.items():
                    for n2,v2 in next.items():
                        v=v1 & v2
                        if np.count_nonzero(v):
                            cur[n1+"_"+n2]=v
        return cur
                

    def partition(self,field,minimum=None,maximum=None,num=None):
        field=self.datafield(field)
        maxWasNone = maximum is None
        if minimum is None:
            minimum = self.fieldmin(field)
        if maximum is None:
            maximum = self.fieldmax(field)
        if num is None:
            if self.isCategorical(field):
                edges=np.array(self.intValues(field))
                edges=edges[(edges>=minimum) & (edges<=maximum)]
                if maxWasNone:
                    edges=np.append(edges,maximum+1)
            else:
                edges=np.linspace(minimum,maximum,11)
        else:
            edges=np.linspace(minimum,maximum,num+1)

        ret={}
        for i in range(len(edges)-1):
            if num is None and self.isCategorical(field):
                nm=self.stringValue(field,edges[i])
            else:
                nm="%.2f-%.2f"%(edges[i],edges[i+1])
            if i == len(edges)-2 and maxWasNone:
                dt=(self.data[field]>=edges[i]) & (self.data[field]<=edges[i+1])
            else:
                dt=(self.data[field]>=edges[i]) & (self.data[field]<edges[i+1])
            if np.count_nonzero(dt):
                ret[nm]=dt
        return ret

    def setPartition(self,keep):
        """Set the current partition filter's keep."""
        self.partitionfilter.setkeep(keep)

    def clearPartition(self):
        """Set the current partition filter's keep."""
        self.partitionfilter.setkeep(np.ones(self.data.shape,dtype=bool))

    def datacol(self,field,filtered=False,respectPartition=True):
        origfield=field
        field=self.datafield(field)
        keep=np.ones(self.data.shape,dtype=bool)

        if respectPartition:
            keep &=self.partitionfilter.getKeep()
        if filtered:
            keep &=self.rootfilter.getKeep()

        if DataModel.genColSeparator in origfield:
            parts=origfield.split(DataModel.genColSeparator)
            assert parts[1] == "cmp" # Only one that should appear right now
            group1=int(parts[2])
            group2=int(parts[3])
            validpairs=(self.cmparray[:,group1] != -1) & (self.cmparray[:,group2] != -1)
            keepvalid=(keep[self.cmparray[validpairs,group1].astype(int)]) & (keep[self.cmparray[validpairs,group2].astype(int)])
            finalret=self.cmparray[keepvalid,group1]
            return self.data[field][finalret.astype(int)]
        return self.data[field][keep]

    def stackedDataCol(self,field,filtered=False,defaultcolor='b',respectPartition=True, keep=None,stacks=0):
        """Return [[arrays],[colors],[labels]]"""
        field=self.datafield(field)
        finalkeep=np.ones(self.data.shape,dtype=bool)
        if keep is not None:
            finalkeep &=keep
        if respectPartition:
            finalkeep &=self.partitionfilter.getKeep()
        if filtered:
            finalkeep &=self.rootfilter.getKeep()

        if stacks == 0:
            stacks=self.stacks
        if stacks is None:
            return [[ self.data[field][finalkeep] ], [defaultcolor] , ["all"] ]
        else:
            a=[]
            c=[]
            l=[]
            for i in range(len(stacks[0])):
                d= self.data[field][finalkeep&stacks[0][i]]
                if len(d):
                    a.append(d)
                    c.append(stacks[1][i])
                    l.append(stacks[2][i])
        return [a,c,l]
                
            

    def setStacks(self,stack):
        self.stacks=stack
        self.stackChange.emit()

    def hasComparisons(self):
        return self.cmparray is not None and len(self.cmparray.shape) > 1

    def cmpvalues(self,field):
        assert self.hasComparisons()
        field=self.datafield(field)
        values=np.ones(self.cmparray.shape)*-1
        valid=self.cmparray != -1
        values[valid]=self.data[field][self.cmparray[valid].astype(int)]
        return values


        

#########################################
###            Load/Save              ###
#########################################

    def onSortChange(self):
        outarr=np.arange(len(self.data))
        if len(self.sortlst):
            outarr=np.argsort(self.data,order=self.sortlst)
            if self.reverseSort:
                outarr = np.flipud(outarr)
        self.data[DataModel.sortColumnName][outarr]=np.arange(len(self.data))
        self._filtered=None
        self.sortchange.emit()

    def outArrIndices(self,applyLimit=True):
        outarr=np.arange(len(self.filtered))
        if len(self.sortlst):
            outarr=np.argsort(self.filtered,order=self.sortlst)
            if self.reverseSort:
                outarr = np.flipud(outarr)
        if applyLimit and self.limit is not None and len(outarr) > self.limit:
            if self.limitModeRandom:
                keep=np.random.permutation(np.arange(len(outarr)))[:self.limit] # Select random set of size self.limit
                outarr=outarr[np.sort(keep)] # Maintain previous sorting order by sorting keep
            else:
                outarr=outarr[:self.limit]
        return outarr

    def saveByPartitions(self,fname,function,partitions=None,internal=True,appendName=True):
        if partitions is not None:
            previous=self.partitionfilter.getKeep()
            self.internalFilterChanges = internal
            for k,v in sorted(partitions.items()):
                self.setPartition(v)
                if np.count_nonzero(self.rootfilter.getKeep()):
                    if appendName:
                        function(fname+"_"+k)
                    else:
                        function(fname,k)
            self.setPartition(previous)
            self.internalFilterChanges = False
        else:
            function(fname)


    def rawColumn(self,c,rows):
        """Original (as-loaded) values of column c for data rows `rows`, for dat export."""
        if c in self.digitized and self.canAccessDigitized(c):
            return self.digitized[c][self.data[c][rows]]
        if c in self.rawcols:
            return self.rawcols[c][rows]
        if self.npfile is not None and "rdata" in self.npfile.keys(): # npz written by DatView <= 2019
            return self.npfile["rdata"][c][rows]
        return self.data[c][rows]

    def selectedRows(self,applyLimit=True):
        """Row indices into self.data for the current selection, in output order."""
        return np.flatnonzero(self.rootfilter.getKeep())[self.outArrIndices(applyLimit)]

    def saveSelDat(self,fname):
        rows=self.selectedRows()
        sep=self.cfg.sep if self.cfg.sep is not None else "\t"
        if self.hasComparisons():
            outcols=list(self.cols)
            columns=[self.data[c][rows] for c in outcols]
        else:
            outcols=[c for c in self.cols if c != DataModel.sortColumnName]
            columns=[self.rawColumn(c,rows) for c in outcols]
        if self.hdrline is not None and not self.hasComparisons():
            hline=self.hdrline
        elif self.cfg.commentchar is None:
            hline=sep.join(outcols)
        else:
            hline=sep.join((self.cfg.commentchar,*outcols))
        text=[]
        for c,col in zip(outcols,columns):
            fmt=self.cfg.fmt(c)
            if col.dtype.kind in "US":
                text.append(col.astype(str))
            else:
                text.append(np.char.mod(fmt,col))
        with open(fname,'w') as fout:
            fout.write(hline+"\n")
            if len(rows):
                lines=text[0]
                for t in text[1:]:
                    lines=np.char.add(np.char.add(lines,sep),t)
                fout.write("\n".join(lines.tolist()))
                fout.write("\n")
        print ("Wrote",fname)

    def canSaveLst(self):
        return 'ifile' in self.cols or ((GroupMgr.prefix + "ifile") in self.cols and self.groupmgr is not None)

    def saveSelLst(self,fname):
        assert self.canSaveLst()
        rows=self.selectedRows()
        ifile=self.datafield('ifile')
        hasevent='event' in self.cols
        with open(fname,'w') as fout:
            for r in rows:
                name=self.stringValue(ifile,self.data[ifile][r])
                if hasevent and self.data['event'][r] != -1:
                    fout.write('%s //%i\n'%(name,self.data['event'][r]))
                else:
                    fout.write('%s\n'%name)
        print ("Wrote",fname)

    def canSaveStream(self):
        return ('sfile' in self.cols or ((GroupMgr.prefix+"sfile") in self.cols and self.groupmgr is not None)) \
                and 'istart' in self.cols and 'cstart' in self.cols and 'cend' in self.cols

    def streamRows(self,rows=None):
        """(stream path, istart, iend, cstart, cend) for each selected row, in output order."""
        if rows is None:
            rows=self.selectedRows()
        sfile=self.datafield('sfile')
        paths=[self.stringValue(sfile,v) for v in self.data[sfile][rows]]
        return zip(paths,self.data['istart'][rows],self.data['iend'][rows],
                   self.data['cstart'][rows],self.data['cend'][rows])

    def saveSelStream(self,fname):
        """Write the selection as a stream. Each crystal becomes its own chunk, so a subset
        of the crystals on a multi-crystal frame can be exported."""
        assert self.canSaveStream()
        streamio.write_selection(self.streamRows(),fname)
        print ("Wrote",fname)

    def pipeSelStream(self,cmd,progress=None):
        """Feed the selection to process_hkl/partialator on stdin; no stream file is written.
        Returns the program's exit code (None if aborted through `progress`)."""
        assert self.canSaveStream()
        return streamio.pipe_selection(self.streamRows(),cmd,progress)

    def mergeByPartitions(self,cmd,partitions=None,progress=None):
        """Run one merge per partition, suffixing the -o output name with the partition name."""
        results={}
        if partitions is None:
            results[""]=self.pipeSelStream(cmd,progress)
            return results
        previous=self.partitionfilter.getKeep()
        self.internalFilterChanges=True
        try:
            for k,v in sorted(partitions.items()):
                self.setPartition(v)
                if self.selectedCount():
                    results[k]=self.pipeSelStream(streamio.resolve_output_name(cmd,k),progress)
        finally:
            self.setPartition(previous)
            self.internalFilterChanges=False
        return results

    def canSaveNumpy(self):
        return self.npfile is None # Don't resave a numpy file as a numpy file. Too complicated.

    def saveAllNumpy(self,fname):
        assert self.canSaveNumpy()
        raw={"raw__"+c:v for c,v in self.rawcols.items()}
        np.savez_compressed(fname,data=self.data,digitizedkeys=np.asarray(sorted(self.digitized.keys())),
                            rawcolkeys=np.asarray(sorted(self.rawcols.keys()),dtype=str),
                            hdrline=np.asarray(self.hdrline or ""),**raw,**self.digitized)

    def saveFilters(self,fname):
        root=ElementTree.Element("filters")
        self.topfilter.toXML(root)
        et=ElementTree.ElementTree(root)
        et.write(fname,pretty_print=True)
        print("Wrote",fname)

    def loadFilters(self,fname):
        self.topfilter.setActive(False) #Invalidate current, easier than deleting
        self.selfilters = {} # clear these
        et=ElementTree.parse(fname)
        root = et.getroot()
        assert root.tag == "filters" and len(list(root)) == 1
        child=root[0]
        if child.tag == "and":
            self.topfilter=AndFilter(self.data.shape)
        elif child.tag == "or":
            self.topfilter=OrFilter(self.data.shape)
        else:
            assert False # Top filter must be group filter
        self.topfilter.setActive(child.get("active") == "True")
        self.loadFilterRecursive(child,self.topfilter)
        self.rootfilter.addChild(self.topfilter)
        self.filterModelChange.emit(self.topfilter)
        self.applyFilters()

    def loadFilterRecursive(self,xmlEl,filterpar):
        for child in xmlEl:
            if child.tag == "or":
                f=OrFilter(self.data.shape)
                self.loadFilterRecursive(child,f)
            elif child.tag == "and":
                f=AndFilter(self.data.shape)
                self.loadFilterRecursive(child,f)
            elif child.tag == "pairbetween":
                if not self.hasComparisons():
                    continue # Don't load this filter if no comparison array
                tmp=AndFilter(self.data.shape)
                self.loadFilterRecursive(child,tmp)
                if len(tmp.children) != 2:
                    continue # Something went wrong, maybe field doesn't exist
                
                field=tmp.children[0].field
                group1=int(child.get("col1"))
                c1=BetweenFilter(tmp.children[0].minimum,tmp.children[0].maximum,self.cmpvalues(field)[:,group1],field)
                

                field=tmp.children[1].field
                group2=int(child.get("col2"))
                c2=BetweenFilter(tmp.children[1].minimum,tmp.children[1].maximum,self.cmpvalues(field)[:,group2],field)

                nm1=DataModel.cmpColTemplate%(field,group1,group2)
                nm2=DataModel.cmpColTemplate%(field,group2,group1)

                if field in self.selfilters and self.selfilters[field] == tmp.children[0]:
                    del self.selfilters[field]
                if nm1 not in self.selfilters:
                    self.selfilters[nm1]=c1
                if nm2 not in self.selfilters:
                    self.selfilters[nm2]=c2

                f=PairBetweenFilter(self.data.shape,self.cmparray,c1,c2,group1,group2,child.get("namestr"))
            else: # field filter
                if self.datafield(child.get("field")) not in self.cols:
                    continue # Filter doesn't apply
                if child.tag == "between":
                    f=BetweenFilter(float(child.get("min")),float(child.get("max")),self.data[self.datafield(child.get("field"))],child.get("field"))
                    if child.get("field") not in self.selfilters:
                        self.selfilters[child.get("field")] = f
                elif child.tag == "greaterequal":
                    f=GreaterEqualFilter(float(child.get("min")),self.data[self.datafield(child.get("field"))],child.get("field"))
                elif child.tag == "lessthan":
                    f=LessThanFilter(float(child.get("max")),self.data[self.datafield(child.get("field"))],child.get("field"))
                elif child.tag == "inset":
                    allowedlst=child.get("set").split(",")
                    allowedset=set()
                    for v in allowedlst:
                        allowedset.add(self.intValue(child.get("field"),v))
                    f=InSetFilter(allowedset,self.data[self.datafield(child.get("field"))],child.get("field"),self.stringValue)
                elif child.tag == "max":
                    if not self.hasComparisons():
                        continue # Don't load this filter if no comparison array
                    f=MaxFilter(self.data.shape,self.cmparray,self.cmpvalues(child.get("field")),child.get("field"))
                elif child.tag == "min":
                    if not self.hasComparisons():
                        continue # Don't load this filter if no comparison array
                    f=MinFilter(self.data.shape,self.cmparray,self.cmpvalues(child.get("field")),child.get("field"))
                elif child.tag == "allbetween":
                    if not self.hasComparisons():
                        continue # Don't load this filter if no comparison array
                    f=AllBetweenFilter(self.data.shape,self.cmparray,self.cmpvalues(child.get("field")),child.get("field"),\
                        float(child.get("min")),float(child.get("max")))
                elif child.tag == "anybetween":
                    if not self.hasComparisons():
                        continue # Don't load this filter if no comparison array
                    f=AnyBetweenFilter(self.data.shape,self.cmparray,self.cmpvalues(child.get("field")),child.get("field"),\
                        float(child.get("min")),float(child.get("max")))
                elif child.tag == "allsame":
                    if not self.hasComparisons():
                        continue # Don't load this filter if no comparison array
                    f=AllSameFilter(self.data.shape,self.cmparray,self.cmpvalues(child.get("field")),child.get("field"))
                elif child.tag == "anydifferent":
                    if not self.hasComparisons():
                        continue # Don't load this filter if no comparison array
                    f=AnyDifferentFilter(self.data.shape,self.cmparray,self.cmpvalues(child.get("field")),child.get("field"))
                else:
                    assert False #Unsupported
            f.setActive(child.get("active") == "True")
            filterpar.addChild(f)

    def hasStreamPeaks(self):
        return ('sfile' in self.cols or \
                  ((GroupMgr.prefix+"sfile") in self.cols and self.groupmgr is not None)) \
                and 'pstart' in self.cols and 'pend' in self.cols

    def _streamBlock(self,datarow,startcol,endcol):
        start=self.data[startcol][datarow]
        end=self.data[endcol][datarow]
        if start < 0 or end < start:
            return []
        path=self.value("sfile",datarow,False)
        try:
            with open(path,'rb') as sfile:
                sfile.seek(start)
                return sfile.read(end-start).decode(errors='replace').splitlines()
        except FileNotFoundError:
            print("Unable to open stream file %s"%path)
            return []

    def streamPeaks(self,datarow):
        peakx=[]
        peaky=[]
        for line in self._streamBlock(datarow,"pstart","pend"):
            l=line.split()
            try:
                peakx.append(float(l[0]))
                peaky.append(float(l[1]))
            except (ValueError,IndexError):
                pass # column header line
        return peakx, peaky

    def hasStreamReflections(self):
        return ('sfile' in self.cols or \
                  ((GroupMgr.prefix+"sfile") in self.cols and self.groupmgr is not None)) \
                and 'rstart' in self.cols and 'rend' in self.cols

    def streamReflections(self,datarow):
        peakx=[]
        peaky=[]
        hkl=[]
        for line in self._streamBlock(datarow,"rstart","rend"):
            l=line.split()
            try:
                x=float(l[7])
                y=float(l[8])
            except (ValueError,IndexError):
                continue # column header line
            peakx.append(x)
            peaky.append(y)
            hkl.append(' '.join(l[0:3]))
        return peakx, peaky, hkl
