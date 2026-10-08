import React from 'react';
import {Composition} from 'remotion';
import film from './data/film.json';
import {Film} from './Film';

export const Root: React.FC = () => (
  <Composition id="Main" component={Film} width={film.width} height={film.height} fps={film.fps} durationInFrames={Math.floor(film.duration * film.fps)} />
);
