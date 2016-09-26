import simplejson, sys, shutil, os, ast , re
from mpipe import OrderedStage , Pipeline
import glob, json, uuid, logging  , time ,datetime 
import subprocess, threading,traceback
from collections import OrderedDict
from pprint import pprint , pformat
import parallel_tools as parallel
from mpipe import OrderedStage , Pipeline
import contig_id_mapping as c_mapping 
import script_util
import handler_utils as handler_util
from biokbase.workspace.client import Workspace
from biokbase.auth import Token
import multiprocessing as mp
from biokbase.RNASeq import rnaseq_util
import doekbase.data_api
from doekbase.data_api.annotation.genome_annotation.api import GenomeAnnotationAPI , GenomeAnnotationClientAPI
from doekbase.data_api.sequence.assembly.api import AssemblyAPI , AssemblyClientAPI
import requests.packages.urllib3
requests.packages.urllib3.disable_warnings()
try:
    from biokbase.HandleService.Client import HandleService
except:
    from biokbase.AbstractHandle.Client import AbstractHandle as HandleService
from biokbase.RNASeq.HiSat2 import HiSat2
from AssemblyUtil.AssemblyUtilClient import AssemblyUtil
from GenomeFileUtil.GenomeFileUtilClient import GenomeFileUtil
#import ExecutionBase.ExecutionBase as ExecutionBase

class HiSat2SampleSetException(Exception):
    pass

class HiSat2SampleSet(HiSat2): 

    def __init__(self, logger, directory, urls):
        super(HiSat2SampleSet, self).__init__(logger, directory, urls)

        # user defined shared variables across methods
        self.sample = None
        self.sampleset_info = None
        #self.num_threads = None


    def prepare(self): 
        # for quick testing, we recover parameters here
        ws_client = self.common_params['ws_client']
        hs = self.common_params['hs_client']
        params = self.method_params
        logger = self.logger
        token = self.common_params['user_token']
        hisat2_dir = self.directory

        try:
               sample,annotation_name = ws_client.get_objects(
                                        [{ 'name' : params['sampleset_id'], 'workspace' : params['ws_id']},
                                        { 'name' : params['genome_id'], 'workspace' : params['ws_id']}])
               self.sample = sample
        except Exception,e:
               logger.exception("".join(traceback.format_exc()))
               raise ValueError(" Error Downloading objects from the workspace ")
            ### Get obejct IDs
        sampleset_info,annotation_info = ws_client.get_object_info_new({"objects": [
                                           {'name': params['sampleset_id'], 'workspace': params['ws_id']},
                                           {'name': params['genome_id'], 'workspace': params['ws_id']}
                                           ]})
        self.sampleset_info = sampleset_info
        ### Get the workspace object ids for the objects ###
        sampleset_id = str(sampleset_info[6]) + '/' + str(sampleset_info[0]) + '/' + str(sampleset_info[4])
        annotation_id = str(annotation_info[6]) + '/' + str(annotation_info[0]) + '/' + str(annotation_info[4])
        sample_type = sampleset_info[2].split('-')[0]

        ### Check if the Library objects exist in the same workspace
        if sample_type != 'KBaseRNASeq.RNASeqSampleSet':
            raise HiSat2SampleSetException('RNASeqSampleSet is required')
        logger.info("Check if the Library objects do exist in the current workspace")
        reads = sample['data']['sample_ids']
        r_label = sample['data']['condition']
        reads_type= sample['data']['Library_type']
        if reads_type == 'PairedEnd': r_type = 'KBaseAssembly.PairedEndLibrary'
        else: r_type = 'KBaseAssembly.SingleEndLibrary'
        e_ws_objs = script_util.if_ws_obj_exists(None,ws_client,params['ws_id'],r_type,reads)
        missing_objs = [i for i in reads if not i in e_ws_objs]
        if len(e_ws_objs) != len(reads):
            raise HiSat2SampleSetException('Missing Library objects {0} in the {1}. please copy them and run this method'.format(",".join(missing_objs),params['ws_id']))
 
        self.num_jobs = len(reads)
	ref_info = ws_client.get_object_info_new({"objects": [{'name': params['genome_id'], 'workspace': params['ws_id']}]})[0]
        ref_id = str(ref_info[6]) + '/' + str(ref_info[0]) + '/' + str(ref_info[4])
	fasta_file =  os.path.join(hisat2_dir,params['genome_id'] + ".fa")
        fasta_file = rnaseq_util.get_fasta_from_genome(logger,ws_client,self.urls,ref_id,fasta_file)	
	#fasta_file = script_util.generate_fasta(logger,self.urls,token,annotation_id,hisat2_dir,params['genome_id'])
        #logger.info("Sanitizing the fasta file to correct id names {}".format(datetime.datetime.utcnow()))
        #mapping_filename = c_mapping.create_sanitized_contig_ids(fasta_file)
        #c_mapping.replace_fasta_contig_ids(fasta_file, mapping_filename, to_modified=True)
        #logger.info("Generating FASTA file completed successfully : {}".format(datetime.datetime.utcnow()))
        hisat2base =os.path.join(hisat2_dir,handler_util.get_file_with_suffix(hisat2_dir,".fa"))
        hisat2base_cmd = '{0} {1}'.format(fasta_file,hisat2base)
	try:
            logger.info("Building Index for Hisat2 {0}".format(hisat2base_cmd))
            cmdline_output = script_util.runProgram(logger,"hisat2-build",hisat2base_cmd,None,hisat2_dir)
        except Exception,e:
            raise Exception("Failed to run command {0}".format(hisat2base_cmd))
        ws_gtf = params['genome_id']+"_GTF"
        ret = script_util.if_obj_exists(None,ws_client,params['ws_id'],"KBaseRNASeq.GFFAnnotation",[ws_gtf])
        if not ret is None:
            logger.info("GFF Annotation Exist for Genome Annotation {0}.... Skipping step ".format(params['genome_id']))
            annot_name,annot_id = ret[0]
            gtf_obj=ws_client.get_objects([{'ref' : annot_id}])[0]
            gtf_id=gtf_obj['data']['handle']['id']
            gtf_name=gtf_obj['data']['handle']['file_name']
 	    try:
                     script_util.download_file_from_shock(logger, shock_service_url=self.urls['shock_service_url'], shock_id=gtf_id,filename=gtf_name, directory=hisat2_dir,token=token)
                     gtf_file = os.path.join(hisat2_dir,gtf_name)
            except Exception,e:
                        raise Exception( "Unable to download shock file, {0}".format(gtf_name))
        else:
	     rnaseq_util.create_gtf_annotation_from_genome(logger,ws_client,hs,self.urls,params['ws_id'],ref_id,params['genome_id'],hisat2_dir,token)
	     #script_util.create_gtf_annotation_from_genome(logger,ws_client,hs,self.urls,params['ws_id'],ref_id,params['genome_id'],fasta_file,hisat2_dir,token)
             #script_util.create_gtf_annotation(logger,ws_client,hs,self.urls,params['ws_id'],annotation_id,params['genome_id'],fasta_file,hisat2_dir,token)
	# Determine the num_threads provided by the user otherwise default the number of threads to 2
 
        count = 0
        logger.info(" Number of threads used by each process {0}".format(self.num_threads))
        for i in reads:
            try:
                    label = r_label[count]
                    task_param = {'job_id' : i,
                                  'label' : r_label[count],
                                  'ws_id' : params['ws_id'],
                                  'reads_type' : reads_type,
                                  'hisat2_dir' : self.directory,
                                  'annotation_id': ref_id, # Changed annotation_id to ref_id for genome object 
                                  'sampleset_id' : sampleset_id
                                 }
                    self.task_list.append(task_param)
                    count = count + 1
            except Exception,e:
                    raise


    def collect(self) :
        # do with 
        alignmentSet_name = self.method_params['sampleset_id']+"_hisat2_AlignmentSet"
        self.logger.info(" Creating AlignmentSet for the Alignments {0}".format(alignmentSet_name))
        # TODO: Split alignment set and report method
        reportObj=script_util.create_RNASeq_AlignmentSet_and_build_report(self.logger,self.common_params['ws_client'],self.method_params['ws_id'],self.sample['data']['sample_ids'],self.task_list[0]['sampleset_id'],self.task_list[0]['annotation_id'],None,self.results,alignmentSet_name)
        reportName = 'Align_Reads_using_Hisat2_'+str(hex(uuid.getnode()))
        report_info = self.common_params['ws_client'].save_objects({
                                                'id':self.sampleset_info[6],
                                                'objects':[
                                                {
                                                'type':'KBaseReport.Report',
                                                'data':reportObj,
                                                'name':reportName,
                                                'meta':{},
                                                'hidden':1, # important!  make sure the report is hidden
                                                #'provenance':provenance
                                                }
                                                ]
                                                })[0]

        returnVal = { "report_name" : reportName,"report_ref" : str(report_info[6]) + '/' + str(report_info[0]) + '/' + str(report_info[4]) }

