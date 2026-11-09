import {useEffect,useRef,useState} from 'react';
import * as THREE from 'three';

/** A local architectural study, rendered independently from the reference product. */
export default function ArchitectureScene(){
 const mount=useRef<HTMLDivElement>(null),[ready,setReady]=useState(false);
 useEffect(()=>{
  const host=mount.current!;let renderer:THREE.WebGLRenderer;
  try{renderer=new THREE.WebGLRenderer({alpha:true,antialias:true,powerPreference:'low-power'})}catch{return}
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.setClearColor(0x141617,0);
  host.appendChild(renderer.domElement);renderer.domElement.setAttribute('aria-hidden','true');
  const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(31,1,.1,100);camera.position.set(10,7,11);camera.lookAt(0,1,0);
  const sculpture=new THREE.Group();scene.add(sculpture);
  scene.add(new THREE.AmbientLight(0xffffff,1.25));
  const key=new THREE.DirectionalLight(0xffdfab,4.3);key.position.set(5,10,6);scene.add(key);
  const rim=new THREE.DirectionalLight(0xafb9cb,2.2);rim.position.set(-7,5,-6);scene.add(rim);
  const materials=[new THREE.MeshStandardMaterial({color:0x3c4040,metalness:.55,roughness:.38}),new THREE.MeshStandardMaterial({color:0xdec28d,metalness:.6,roughness:.32}),new THREE.MeshStandardMaterial({color:0x222626,metalness:.4,roughness:.5})];
  const edges=new THREE.LineBasicMaterial({color:0xd4b77f,transparent:true,opacity:.35});
  function block(x:number,y:number,z:number,w:number,h:number,d:number,material=0){const geo=new THREE.BoxGeometry(w,h,d),mesh=new THREE.Mesh(geo,materials[material]);mesh.position.set(x,y+h/2,z);sculpture.add(mesh);const line=new THREE.LineSegments(new THREE.EdgesGeometry(geo),edges);line.position.copy(mesh.position);sculpture.add(line)}
  block(0,-.18,0,6.5,.18,5.4,2);
  for(let floor=0;floor<6;floor++){
   const y=floor*.62,shift=Math.sin(floor*.48)*.32;
   block(shift,y,0,3.8,.14,3.3,floor===4?1:0);
   block(-1.42+shift,y+.14,.08,.2,.48,2.95);
   block(1.42+shift,y+.14,.08,.2,.48,2.95);
   block(shift,y+.14,-1.37,2.7,.48,.2,2);
   for(let column=0;column<6;column++)block(-1.24+column*.49+shift,y+.14,1.38,.055,.48,.09,1);
  }
  block(1.95,.0,-1.7,1.28,2.05,1.15,2);block(1.95,2.05,-1.7,1.4,.13,1.3,1);
  block(-2.2,.0,1.65,1.2,.24,1.6,1);
  const grid=new THREE.GridHelper(14,28,0x72654e,0x333938);grid.position.y=-.2;scene.add(grid);
  const resize=()=>{const {width,height}=host.getBoundingClientRect();if(!width||!height)return;renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix()};
  const observer=new ResizeObserver(resize);observer.observe(host);resize();
  let frame=0,drag=false,lastX=0,angle=-.22,lastTime=0;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const down=(e:PointerEvent)=>{drag=true;lastX=e.clientX;host.setPointerCapture(e.pointerId)};
  const move=(e:PointerEvent)=>{if(drag){angle+=(e.clientX-lastX)*.009;lastX=e.clientX}};
  const up=()=>{drag=false};
  host.addEventListener('pointerdown',down);host.addEventListener('pointermove',move);host.addEventListener('pointerup',up);host.addEventListener('pointercancel',up);
  const tick=(time:number)=>{frame=requestAnimationFrame(tick);if(time-lastTime<32||document.hidden)return;const delta=Math.min(50,time-lastTime);lastTime=time;if(!drag&&!reduced.matches)angle+=delta*.00005;sculpture.rotation.y=angle;renderer.render(scene,camera)};
  frame=requestAnimationFrame(tick);setReady(true);
  return()=>{cancelAnimationFrame(frame);observer.disconnect();host.removeEventListener('pointerdown',down);host.removeEventListener('pointermove',move);host.removeEventListener('pointerup',up);host.removeEventListener('pointercancel',up);scene.traverse(item=>{const mesh=item as THREE.Mesh;if(mesh.geometry)mesh.geometry.dispose()});materials.forEach(m=>m.dispose());edges.dispose();(grid.material as THREE.Material).dispose();renderer.dispose();renderer.domElement.remove()};
 },[]);
 return <div className="architecture-scene" ref={mount} aria-label="建筑体块视觉示意，可拖动旋转"><div className="architecture-scene-label">FORM · SPACE · LIGHT</div>{!ready&&<svg className="architecture-fallback" viewBox="0 0 420 320" aria-hidden="true">{[0,1,2,3,4,5].map(i=><path key={i} d={`M95 ${214-i*24}L215 ${262-i*24}L330 ${196-i*24}L210 ${150-i*24}Z`} fill={i===3?'#dec28d':'#252b2a'} stroke="#9a835d" strokeWidth="1.5"/>)}</svg>}<span className="architecture-scene-hint">建筑体块研究 <span>拖动旋转</span></span></div>
}
