import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import sharp from 'sharp';
import {detectCalibration} from '../soren91/calibration.mjs';
import {analyzeScreenshot} from '../soren91/screenshot_analyzer.mjs';
import {hasReadyBanner} from '../soren91/ready_banner.mjs';
const bytes=readFileSync(new URL('./fixtures/soren91-sprites/ready_20261015.jpg',import.meta.url));
const raw=async b=>sharp(b).ensureAlpha().raw().toBuffer({resolveWithObject:true});
for(const width of [1280,960])test(`READY waits without inventing five board pieces at ${width}px`,async()=>{
 const b=await sharp(bytes).resize({width}).png().toBuffer();const{data,info}=await raw(b);const cal=detectCalibration(data,info.width,info.height);
 const state=await analyzeScreenshot(b,cal);assert.equal(state.state,'WAITING');assert.deepEqual(state.pieces,[]);assert.equal(state.next,null);assert.equal(state.perception.reason,'non-move');
});
test('a current or physical stack prevents an empty-board READY match',async()=>{
 const{data,info}=await raw(bytes);const cal=detectCalibration(data,info.width,info.height);
 for(const [left,top]of [[610,150],[610,550]]){
  const changed=Buffer.from(data);for(let y=top;y<top+40;y++)for(let x=left;x<left+40;x++)changed.set([0,0,0,255],(y*info.width+x)*4);
  assert.equal(hasReadyBanner(changed,info.width,info.height,cal),false);
 }
});
test('similar colours without the READY glyphs are not a waiting banner',async()=>{
 const{data,info}=await raw(bytes);const cal=detectCalibration(data,info.width,info.height);const changed=Buffer.from(data);
 for(let y=352;y<384;y++)for(let x=552;x<723;x++)changed.set(x%8<4?[220,240,220,255]:[55,30,150,255],(y*info.width+x)*4);
 assert.equal(hasReadyBanner(changed,info.width,info.height,cal),false);
});
test('reviewed country frames retain their existing scene classification',async()=>{
 for(const file of ['game_0011_turn_7.jpg','game_0011_turn_11.jpg','game_0011_turn_14.jpg','game_0013_turn_1.jpg','game_0008_turn_2_20261015.jpg']){
  const b=readFileSync(new URL('./fixtures/soren91-sprites/'+file,import.meta.url));const{data,info}=await raw(b);const cal=detectCalibration(data,info.width,info.height);
  assert.equal(hasReadyBanner(data,info.width,info.height,cal),false,file);
 }
});
