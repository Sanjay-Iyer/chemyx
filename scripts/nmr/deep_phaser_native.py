"""Native CPU evaluator of the authors' unchanged TFJS GraphDefs and weights.

Executed only in the isolated TensorFlow environment. JSON-lines IPC carries
the authors' already-normalized real spectra; the original search stays in JS.
"""
import base64
import json
import os
from pathlib import Path
import sys
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL','2')
os.environ.setdefault('TF_ENABLE_ONEDNN_OPTS','0')
import numpy as np
import tensorflow as tf
from google.protobuf import json_format

ROOT=Path(__file__).resolve().parents[2]/'results/NMR_validation_100226/logs/additional_phase_methods/external_audit/colmarvista'
tf.config.threading.set_intra_op_parallelism_threads(4)
tf.config.threading.set_inter_op_parallelism_threads(1)


def load_model(flag):
    folder=ROOT/f'saved_model_p{flag}'
    graph=json.loads((folder/'model.json').read_text())
    topology=graph['modelTopology'];nodes={n['name']:n for n in topology['node']}
    for group in graph['weightsManifest']:
        data=b''.join((folder/p).read_bytes() for p in group['paths']);offset=0
        for weight in group['weights']:
            if 'quantization' in weight:raise ValueError('Quantized weights not supported by audited adapter')
            dtype={'float32':'<f4','int32':'<i4','bool':'?'}[weight['dtype']]
            count=int(np.prod(weight['shape']));length=count*np.dtype(dtype).itemsize
            array=np.frombuffer(data[offset:offset+length],dtype=dtype).reshape(weight['shape']);offset+=length
            tensor=tf.make_tensor_proto(array)
            nodes[weight['name']]['attr']['value']['tensor']=json_format.MessageToDict(tensor)
        if offset!=len(data):raise ValueError('Weight bytes not completely consumed')
    graphdef=tf.compat.v1.GraphDef();json_format.ParseDict(topology,graphdef)
    wrapped=tf.compat.v1.wrap_function(lambda:tf.compat.v1.import_graph_def(graphdef,name=''),[])
    inputs=[wrapped.graph.get_tensor_by_name(graph['signature']['inputs'][key]['name']) for key in ('main_input','mask_input')]
    output=wrapped.graph.get_tensor_by_name(graph['signature']['outputs']['output_0']['name'])
    return wrapped.prune(inputs,output)


def main():
    models=[load_model(0),load_model(1)]
    print(json.dumps({'ready':True,'tensorflow':tf.__version__}),flush=True)
    for line in sys.stdin:
        try:
            request=json.loads(line)
            data=np.frombuffer(base64.b64decode(request['data']),dtype='<f4').reshape(request['batches'],request['length'],1)
            mask=np.ones((request['batches'],request['length']//128),dtype=bool)
            result=models[request['flag']](tf.convert_to_tensor(data),tf.convert_to_tensor(mask)).numpy()
            print(json.dumps({'probabilities':result.reshape(-1).tolist()}),flush=True)
        except Exception as exc:print(json.dumps({'error':str(exc)}),flush=True)


if __name__=='__main__':main()
