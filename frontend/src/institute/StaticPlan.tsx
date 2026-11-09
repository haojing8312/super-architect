import {departments} from './data';
import {ROOMS} from './scene';

/**
 * Original static fallback for machines without WebGL.
 * It is a hand-drawn axonometric of the same floor plate, not a screenshot
 * and not a copied asset.
 */
export default function StaticInstitutePlan({reason}: {reason?: string}) {
  const scaleX = 1.02;
  const scaleY = 0.5;
  const project = (x: number, z: number) => [x * scaleX - z * scaleX, (x + z) * scaleY];
  const polygon = (x0: number, x1: number, z0: number, z1: number) => {
    const points = [project(x0, z0), project(x1, z0), project(x1, z1), project(x0, z1)];
    return points.map(point => point.join(',')).join(' ');
  };
  const wallFace = (x0: number, x1: number, z: number, height: number) => {
    const [ax, ay] = project(x0, z);
    const [bx, by] = project(x1, z);
    return `M${ax},${ay}L${bx},${by}L${bx},${by + height}L${ax},${ay + height}Z`;
  };

  return (
    <div className="ih-fallback">
      <svg viewBox="-22 -11 44 25" role="img" aria-label="建筑设计院静态平面示意，包含六个部门和中央总师办评审区">
        <defs>
          <linearGradient id="ih-floor" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#efe6d5" />
            <stop offset="1" stopColor="#d8c2a0" />
          </linearGradient>
          <linearGradient id="ih-wall" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#8d8577" />
            <stop offset="1" stopColor="#6f685c" />
          </linearGradient>
        </defs>
        <polygon points={polygon(-11.9, 11.9, -6.9, 6.9)} fill="#1e2223" opacity="0.6" />
        {ROOMS.map(room => (
          <g key={room.dept.id}>
            <polygon points={polygon(room.x0, room.x1, room.z0, room.z1)} fill="url(#ih-floor)" stroke="#bfa97f" strokeWidth="0.06" />
            <path d={wallFace(room.x0, room.x1, room.z0, 1.5)} fill="url(#ih-wall)" opacity="0.85" />
            <path d={wallFace(room.x0, room.x0, room.z1, 1.5)} fill="#7c7466" opacity="0.7" />
          </g>
        ))}
        <polygon points={polygon(5.0, 11.4, -6.4, 6.4)} fill="url(#ih-floor)" stroke="#bfa97f" strokeWidth="0.08" />
        <polygon points={polygon(-11.4, 5.2, -1.4, 1.4)} fill="#cdb48e" stroke="#b09163" strokeWidth="0.05" />
        <path d="M-11.4,0 L5,0" stroke="#b99a62" strokeWidth="0.06" strokeDasharray="0.4 0.3" />
        <ellipse cx={project(8.2, 0.15)[0]} cy={project(8.2, 0.15)[1]} rx="1.75" ry="0.9" fill="#e7ddc9" stroke="#b4945f" strokeWidth="0.06" />
        {ROOMS.map(room => {
          const [x, y] = project((room.x0 + room.x1) / 2, room.z0 > 0 ? room.z0 + 1.1 : room.z1 - 1.1);
          return (
            <g key={'label-' + room.dept.id} transform={`translate(${x},${y})`}>
              <line x1="0" y1="0" x2="0" y2="-1.5" stroke="#c7a86f" strokeWidth="0.05" />
              <rect x="-2.05" y="-2.45" width="4.1" height="0.95" fill="#232726" stroke="#8f7a55" strokeWidth="0.05" />
              <text x="0" y="-1.85" textAnchor="middle" fontSize="0.6" fill="#f0e6d2" fontWeight="600">{room.dept.name}</text>
              <text x="0" y="-1.62" textAnchor="middle" fontSize="0.3" fill="#b9ad93">{room.dept.roomCode}</text>
            </g>
          );
        })}
        <g transform={`translate(${project(8.2, 0.15)[0] - 2.4},${project(8.2, 0.15)[1] - 2.2})`}>
          <rect x="0" y="0" width="4.8" height="1" fill="#232726" stroke="#8f7a55" strokeWidth="0.05" />
          <text x="2.4" y="0.65" textAnchor="middle" fontSize="0.6" fill="#f0e6d2" fontWeight="600">{departments[6].name}</text>
        </g>
        {[
          [-8.6, -4.6], [-3.4, -3.4], [1.2, -3.4], [-8.55, 4.4], [-4.9, 3.8], [1.1, 3.2], [7.15, 1.35],
        ].map(([x, z], index) => {
          const [px, py] = project(x, z);
          return (
            <g key={index} transform={`translate(${px},${py})`}>
              <ellipse cx="0" cy="0.32" rx="0.34" ry="0.16" fill="#2a2d2b" opacity="0.35" />
              <path d="M-0.2,0.3 L-0.16,-0.36 L0.16,-0.36 L0.2,0.3 Z" fill={index === 6 ? '#3a3f42' : '#4c5354'} />
              <circle cx="0" cy="-0.55" r="0.15" fill="#d3a483" />
              <path d="M-0.16,-0.62 Q0,-0.78 0.16,-0.62" stroke="#2a211c" strokeWidth="0.09" fill="none" />
            </g>
          );
        })}
      </svg>
      <div className="ih-fallback-note">
        <strong>三维场景不可用</strong>
        <span>{reason || '当前环境未启用 WebGL，已显示静态院区图。'}部门入口与任务面板仍可正常使用。</span>
      </div>
    </div>
  );
}
