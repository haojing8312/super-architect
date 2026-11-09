import {useEffect, useRef, useState} from 'react';
import {Move3D} from 'lucide-react';
import {createInstitute, type InstituteHandle} from './institute/scene';
import type {DepartmentId} from './institute/data';
import StaticInstitutePlan from './institute/StaticPlan';

export type ArchitectureSceneProps = {
  paused?: boolean;
  reducedMotion?: boolean;
  onEnterDepartment?: (id: DepartmentId) => void;
  onReady?: () => void;
  onStatus?: (status: 'live' | 'static') => void;
};

/**
 * Vessel for the original Three.js institute. All geometry, people and motion
 * are generated in `institute/`; this component only owns mounting, fallback
 * and the playback state required by the host page.
 */
export default function ArchitectureScene({paused = false, reducedMotion = false, onEnterDepartment, onReady, onStatus}: ArchitectureSceneProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const handleRef = useRef<InstituteHandle | null>(null);
  const enterRef = useRef(onEnterDepartment);
  const [failed, setFailed] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [activity, setActivity] = useState('在工位绘图与录入');
  const [focus, setFocus] = useState<'overview' | 'actor'>('overview');
  enterRef.current = onEnterDepartment;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let handle: InstituteHandle | null = null;
    let disposed = false;
    try {
      handle = createInstitute({
        host,
        onEnter: id => enterRef.current?.(id),
        onReady: () => { if (!disposed) { setReady(true); onReady?.(); onStatus?.('live'); } },
      });
    } catch (error) {
      if (import.meta.env.DEV) console.warn('institute scene unavailable', error);
      setFailed('当前环境未启用 WebGL，已切换为静态院区图。');
      onStatus?.('static');
      return;
    }
    handleRef.current = handle;
    const canvas = handle.renderer.domElement;
    const lost = () => { if (!disposed) { setFailed('图形上下文已丢失，已切换为静态院区图。'); onStatus?.('static'); } };
    canvas.addEventListener('webglcontextlost', lost);
    const qaMode = import.meta.env.DEV || new URLSearchParams(window.location.search).has('qa');
    if (qaMode) (window as unknown as {__institute?: unknown}).__institute = {
      pause: () => handle!.setPaused(true),
      play: () => handle!.setPaused(false),
      reduce: (value: boolean) => handle!.setReduced(value),
      focus: (mode: 'overview' | 'actor') => handle!.setFocus(mode),
      portrait: (mode: 'off' | 'body' | 'face') => handle!.portrait(mode),
      seek: (time: number) => handle!.seek(time),
      snapshot: () => handle!.snapshot(),
      enter: (id: DepartmentId) => enterRef.current?.(id),
      resize: () => handle!.resize(),
      landmarks: () => handle!.landmarks(),
      project: (x: number, y: number, z: number) => handle!.project(x, y, z),
    };
    const timer = window.setInterval(() => {
      if (!handleRef.current) return;
      const snap = handleRef.current.snapshot();
      setActivity(String(snap.activity || ''));
    }, 650);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      canvas.removeEventListener('webglcontextlost', lost);
      if ((window as unknown as {__institute?: unknown}).__institute) delete (window as unknown as {__institute?: unknown}).__institute;
      handle?.dispose();
      handleRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    handleRef.current?.setPaused(paused || !!failed);
  }, [paused, failed]);

  useEffect(() => {
    handleRef.current?.setReduced(reducedMotion);
  }, [reducedMotion]);

  return (
    <div className="ih-scene" ref={hostRef} aria-label="建筑设计院三维场景，可拖动旋转，点击部门房间进入">
      {failed ? <StaticInstitutePlan reason={failed} /> : (
        <>
          <div className={'ih-scene-hint' + (ready ? '' : ' is-loading')}>
            <Move3D size={14} />
            <span>{ready ? (focus === 'actor' ? '人物近景 · 拖动查看四周' : '拖动旋转 · 点击部门房间进入') : '正在构建设计院场景…'}</span>
          </div>
          {ready && <div className="ih-scene-hud">
            <span className="ih-hud-kicker">现场</span>
            <strong>建筑设计师</strong>
            <i>{activity}</i>
            <button className="ih-hud-focus" onClick={() => { const next = focus === 'actor' ? 'overview' : 'actor'; setFocus(next); handleRef.current?.setFocus(next); }} aria-pressed={focus === 'actor'}>
              {focus === 'actor' ? '返回院区' : '查看近景'}
            </button>
          </div>}
        </>
      )}
    </div>
  );
}
