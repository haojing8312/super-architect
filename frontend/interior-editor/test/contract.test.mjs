import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {validateScene} = require('../scripts/validate-scene.cjs');
function scene() {
  const generation={taskId:'a'.repeat(32),sourceFileId:'b'.repeat(32),sourceSha256:'c'.repeat(64),page:1};
  return {format:'interior-studio/1.0',engineVersion:'1.0',name:'合成户型',isSample:false,revision:0,generation,
    source:{name:'floor.png',imageData:'data:image/png;base64,YQ=='},
    drawing:{width:4,height:3,sourceWidth:400,sourceHeight:300,scaleX:0.01,scaleZ:0.01,origin:[0,0],status:'user-calibrated'},
    recognition:{status:'manual'},rooms:[],architecture:[{id:'wall',name:'墙',type:'wall',pos:[0,1,0],size:[4,2,0.1],sourceEvidence:{sourceFileId:generation.sourceFileId,sourceSha256:generation.sourceSha256}}],
    schemes:{base:{objects:[]}},style:'base',delivery:{stage:'draft'}};
}
test('accept a source-bound local scene and reject cross-source evidence',()=>{
  const value=scene();assert.equal(validateScene(value),true);
  value.architecture[0].sourceEvidence.sourceSha256='d'.repeat(64);
  assert.throws(()=>validateScene(value),/sourceEvidence/);
});
test('offline scene cannot reference a remote floorplan',()=>{
  const value=scene();value.source.imageData='https://example.invalid/floor.png';
  assert.throws(()=>validateScene(value),/data URI/);
});
