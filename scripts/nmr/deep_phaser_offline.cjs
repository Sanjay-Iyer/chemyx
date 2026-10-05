/* Offline adapter to the authors' actual COLMARvista DEEP Phaser models/search.
 * The external source and models remain unmodified and are separately licensed.
 * Only I/O, cached model loading and a prediction budget differ from the browser.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');
const {spawn} = require('child_process');
const readline = require('readline');
const root = path.resolve(__dirname, '../../results/NMR_validation_100226/logs/additional_phase_methods/external_audit');
const tf = require(path.join(root, 'tfjs-4.22.0.cjs'));
const Module = require('module');
const wasmPath = path.join(root,'wasm/tf-backend-wasm.js');
const wasmModule = new Module(wasmPath,module);
wasmModule.filename=wasmPath;
wasmModule.require=function(name){return name==='@tensorflow/tfjs-core'?tf:require(name);};
wasmModule._compile(fs.readFileSync(wasmPath,'utf8'),wasmPath);
const wasm=wasmModule.exports;
wasm.setWasmPaths(path.join(root,'wasm')+path.sep);
wasm.setThreadsCount(1);
const sourcePath = path.join(root, 'colmarvista/js/1d.js');
const source = fs.readFileSync(sourcePath, 'utf8');

async function loadModel(flag) {
  const folder = path.join(root, `colmarvista/saved_model_p${flag}`);
  const graph = JSON.parse(fs.readFileSync(path.join(folder, 'model.json')));
  const specs = graph.weightsManifest.flatMap(group => group.weights);
  const bytes = Buffer.concat(graph.weightsManifest.flatMap(group => group.paths.map(p => fs.readFileSync(path.join(folder,p)))));
  return tf.loadGraphModel({load: async () => ({modelTopology: graph.modelTopology,
    weightSpecs: specs, weightData: bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),
    signature: graph.signature, format: graph.format, generatedBy: graph.generatedBy, convertedBy: graph.convertedBy})});
}

async function main() {
  const request = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const outputPath = process.argv[3];
  await tf.setBackend('wasm'); await tf.ready();
  const nativePython=path.resolve(root,'../deep_env/Scripts/python.exe');
  const useNative=process.env.DEEP_PHASER_BACKEND!=='wasm' && fs.existsSync(nativePython);
  let nativeChild=null,lines=[],waiters=[];
  function nextLine(){return lines.length?Promise.resolve(lines.shift()):new Promise((resolve,reject)=>waiters.push({resolve,reject}));}
  let nativeVersion=null;
  if(useNative){
    nativeChild=spawn(nativePython,[path.join(__dirname,'deep_phaser_native.py')],{stdio:['pipe','pipe','pipe'],windowsHide:true});
    readline.createInterface({input:nativeChild.stdout}).on('line',line=>{if(waiters.length)waiters.shift().resolve(line);else lines.push(line);});
    nativeChild.stderr.on('data',bytes=>process.stderr.write(bytes));
    nativeChild.on('exit',code=>{while(waiters.length)waiters.shift().reject(Error('Native model process exited '+code));});
    const ready=JSON.parse(await nextLine());if(!ready.ready)throw Error('Native model not ready');nativeVersion=ready.tensorflow;
  }
  const models = useNative?null:await Promise.all([loadModel(0),loadModel(1)]);
  const referenceStart = source.indexOf('function get_data_from_phase_correction(');
  const referenceEnd = source.indexOf('// Wrap the logic in an async function',referenceStart);
  let functions = source.slice(referenceStart,referenceEnd);
  // Stop before the browser's in-place spectrum mutation and UI refresh; this
  // adapter returns endpoint phase values, applied later to the shared FFT.
  const mutationStart = functions.indexOf('        let phase_array = new Float32Array');
  const helperStart = functions.indexOf('async function get_cross_point_p1(');
  if (referenceStart<0 || mutationStart<0 || helperStart<0) throw Error('Reference source layout changed');
  functions = functions.slice(0,mutationStart)+'        return result;\n    } finally { console.log = original_console_log; }\n}\n'+functions.slice(helperStart);
  let predictions = 0;
  const history = [];
  const maxPredictions = request.maximum_predictions || 500;
  const sink = {value:'',scrollHeight:0,scrollTop:0,innerText:''};
  const context = {Float32Array,Math,console:{log(){}},
    document:{getElementById(){return sink;}},alert(){},disable_enable_phase_baseline_buttons(){},
    all_spectra:[{raw_data:new Float32Array(request.real),raw_data_i:new Float32Array(request.imaginary)}],
    async runPrediction(data,length,flag=0){
      if (++predictions>maxPredictions) throw Error('Prediction budget exceeded; no phase fabricated');
      const batches=data.length/length;
      for(let i=0;i<batches;i++){
        let maximum=-Infinity;
        for(let j=0;j<length;j++)maximum=Math.max(maximum,data[i*length+j]);
        if(!Number.isFinite(maximum)||maximum===0)throw Error('Invalid reference normalization maximum');
        for(let j=0;j<length;j++)data[i*length+j]/=maximum;
      }
      let values;
      if(useNative){
        nativeChild.stdin.write(JSON.stringify({flag,batches,length,data:Buffer.from(data.buffer,data.byteOffset,data.byteLength).toString('base64')})+'\n');
        const response=JSON.parse(await nextLine());if(response.error)throw Error(response.error);values=response.probabilities;
      }else{
        const tensor=tf.tensor(data).reshape([batches,length,1]);
        const mask=tf.ones([batches,length/128],'bool');
        const result=models[flag].predict({'main_input':tensor,'mask_input':mask});
        values=Array.from(result.dataSync());
        tensor.dispose();mask.dispose();result.dispose();
      }
      if(values.some(v=>!Number.isFinite(v)))throw Error('Non-finite model predictions');
      history.push({flag,probabilities:values});
      if(predictions%25===0)process.stderr.write(`DEEP predictions: ${predictions}\n`);
      return Array.from({length:batches},(_,i)=>values.slice(i*3,(i+1)*3));
    }};
  vm.createContext(context);vm.runInContext(functions,context,{filename:sourcePath});
  let result;
  try{
    const angles=await vm.runInContext('run_ann_phase_correction(0)',context);
    if(!angles||angles.length!==2||angles.some(v=>!Number.isFinite(v)))throw Error('Invalid endpoint phases');
    const n=request.real.length, delta=angles[1]-angles[0];
    result={success:true,status:'reference_search_completed',left_endpoint_deg:angles[0],right_endpoint_deg:angles[1],
      p0_deg:angles[1]-delta/n,p1_deg:-delta,inverse_phase:true,
      mapping:'Reversed input: inverse P0=right-(right-left)/N, P1=-(right-left), same full k/N FFT',
      method:'deep_phaser',predictions,history,backend:useNative?'native_tensorflow_cpu':tf.getBackend(),tensorflow_version:nativeVersion,tfjs_version:tf.version.tfjs};
  }catch(error){result={success:false,status:'reference_inference_failed',message:String(error),predictions,history,method:'deep_phaser'};}
  result.source_sha256=crypto.createHash('sha256').update(fs.readFileSync(sourcePath)).digest('hex');
  result.raw_sha256=request.raw_sha256;
  fs.writeFileSync(outputPath,JSON.stringify(result,null,2));
  if(models)for(const model of models)model.dispose();
  if(nativeChild){nativeChild.stdin.end();nativeChild.kill();}
  process.stdout.write(JSON.stringify({success:result.success,predictions:result.predictions,status:result.status,p0:result.p0_deg,p1:result.p1_deg})+'\n');
}
main().catch(error=>{process.stderr.write(String(error)+'\n');process.exitCode=1;});
