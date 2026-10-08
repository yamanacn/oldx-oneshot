import {Config} from '@remotion/cli/config';

Config.setVideoImageFormat('jpeg');
Config.setJpegQuality(95);
Config.setCodec('h264');
Config.setPixelFormat('yuv420p');
// 浏览器用 ANGLE（Windows 上是 D3D11）渲染，走显卡
Config.setChromiumOpenGlRenderer('angle');
// 显卡编码（NVENC）不接受 CRF；render.cmd 里设 NVENC=1 并改传码率
if (process.env.NVENC !== '1') {
  Config.setCrf(17);
}
