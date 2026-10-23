// WebGL 的两件常用工具（three.js + @remotion/three）：
//   <Stage3D x y w h camera>…</Stage3D>   画面某一块里放一个 3D 场景（ThreeCanvas），里面写 R3F 的 <mesh> 等；
//                                            可以配 @react-three/drei 的几何体/文字/环境光，@react-three/postprocessing 的辉光、景深、色差。
//   <PostFX>…</PostFX>                    后期特效（@react-three/postprocessing 的 Bloom、DepthOfField、ChromaticAberration、Noise……）一律包在它里面，
//                                            不要直接用 <EffectComposer>：直接用时 3D 场景挂上后的第一帧是黑的（实测：单帧静帧全黑，并行渲染每个页面的第一帧黑）。
//   <ShaderPlane frag uniforms />           在 Stage3D 里铺满整块的片元着色器（极光底、溶解、扫光、像素格、噪声转场……）。
//                                            着色器里用的时间从 uniforms 传进来（uT: useNow()），不要用 three 的时钟。
// 规则：3D 场景里的一切都由传进去的时间 t 直接算出来；不用 useFrame 累加状态，不用物理引擎的逐帧积分。
import React, {useEffect, useMemo, useState} from 'react';
import {continueRender, delayRender} from 'remotion';
import {ThreeCanvas} from '@remotion/three';
import {useThree} from '@react-three/fiber';
import {EffectComposer} from '@react-three/postprocessing';
import * as THREE from 'three';

export const Stage3D: React.FC<{x?: number; y?: number; w: number; h: number; camera?: {position?: [number, number, number]; fov?: number}; alpha?: boolean; children: React.ReactNode}> = ({x = 0, y = 0, w, h, camera, alpha = true, children}) => (
  <div style={{position: 'absolute', left: x, top: y, width: w, height: h}}>
    <ThreeCanvas width={w} height={h} camera={{position: camera?.position ?? [0, 0, 6], fov: camera?.fov ?? 40}} gl={{antialias: true, alpha, premultipliedAlpha: false, preserveDrawingBuffer: true}}>
      {children}
    </ThreeCanvas>
  </div>
);

const VERT = 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0., 1.); }';

/**
 * 铺满所在 Stage3D 的片元着色器。uniforms 的值可以是数字、[x,y]、[r,g,b]，每帧都会更新。
 * 默认当背景用：不透明、最先画，3D 物体画在它上面。要当盖在上面的遮罩/转场用时设 overlay（透明、最后画）。
 */
export const ShaderPlane: React.FC<{frag: string; uniforms: Record<string, number | number[]>; overlay?: boolean}> = ({frag, uniforms, overlay = false}) => {
  const mat = useMemo(() => new THREE.ShaderMaterial({
    vertexShader: VERT, fragmentShader: frag, transparent: overlay, depthTest: false, depthWrite: false,
    uniforms: Object.fromEntries(Object.keys(uniforms).map((k) => [k, {value: null as unknown}])),
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [frag]);
  for (const [k, v] of Object.entries(uniforms)) {
    mat.uniforms[k].value = Array.isArray(v) ? (v.length === 2 ? new THREE.Vector2(v[0], v[1]) : new THREE.Vector3(v[0], v[1], v[2])) : v;
  }
  return <mesh material={mat} frustumCulled={false} renderOrder={overlay ? 999 : -1}><planeGeometry args={[2, 2]} /></mesh>;
};

/** 片元着色器里常用的噪声函数（simplex 2D + fbm + hash），拼到自己的 frag 前面用 */
export const GLSL_NOISE = `
vec3 mod289(vec3 x){return x-floor(x*(1./289.))*289.;}
vec2 mod289(vec2 x){return x-floor(x*(1./289.))*289.;}
vec3 permute(vec3 x){return mod289(((x*34.)+1.)*x);}
float snoise(vec2 v){const vec4 C=vec4(.211324865405187,.366025403784439,-.577350269189626,.024390243902439);
vec2 i=floor(v+dot(v,C.yy));vec2 x0=v-i+dot(i,C.xx);vec2 i1=(x0.x>x0.y)?vec2(1.,0.):vec2(0.,1.);
vec4 x12=x0.xyxy+C.xxzz;x12.xy-=i1;i=mod289(i);vec3 p=permute(permute(i.y+vec3(0.,i1.y,1.))+i.x+vec3(0.,i1.x,1.));
vec3 m=max(.5-vec3(dot(x0,x0),dot(x12.xy,x12.xy),dot(x12.zw,x12.zw)),0.);m=m*m;m=m*m;
vec3 x=2.*fract(p*C.www)-1.;vec3 h=abs(x)-.5;vec3 ox=floor(x+.5);vec3 a0=x-ox;
m*=1.79284291400159-.85373472095314*(a0*a0+h*h);vec3 g;g.x=a0.x*x0.x+h.x*x0.y;g.yz=a0.yz*x12.xz+h.yz*x12.yw;return 130.*dot(m,g);}
float fbm(vec2 p){float s=0.,a=.5;for(int i=0;i<5;i++){s+=a*snoise(p);p=p*2.02+vec2(1.7,9.2);a*=.5;}return s*.5+.5;}
float hash(vec2 p){return fract(sin(dot(p,vec2(12.9898,78.233)))*43758.5453);}
`;

/** 后期特效的容器。EffectComposer 在自己的 effect 里才设好尺寸，挂上后的第一帧会渲成黑的；这里挂上后再补渲一次、渲完才放行截图 */
const Settle: React.FC = () => {
  const {advance} = useThree();
  const [h] = useState(() => delayRender('postprocessing warm-up'));
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      advance(performance.now());
      continueRender(h);
    });
    return () => cancelAnimationFrame(id);
  }, [advance, h]);
  return null;
};
export const PostFX: React.FC<React.ComponentProps<typeof EffectComposer>> = ({children, ...props}) => (
  <>
    <EffectComposer multisampling={0} {...props}>{children}</EffectComposer>
    <Settle />
  </>
);
