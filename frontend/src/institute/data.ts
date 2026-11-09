/**
 * Department model for the institute home page.
 *
 * Every department is an entrance to a capability that already exists in the
 * local workbench. Capabilities that are not implemented yet are recorded in
 * `gap` and must never be presented as available.
 */
export type DepartmentId = 'architecture' | 'planning' | 'structure' | 'mep' | 'interior' | 'bim' | 'review';

export type Department = {
  id: DepartmentId;
  name: string;
  plaque: string;
  nameEn: string;
  roomCode: string;
  entry: string;
  entryLabel: string;
  capability: string;
  gap?: string;
  accent: string;
  activity: string;
};

export const departments: Department[] = [
  {
    id: 'architecture',
    name: '建筑设计所',
    plaque: '建筑设计所',
    nameEn: 'ARCHITECTURE STUDIO',
    roomCode: 'A-01',
    entry: 'chat',
    entryLabel: '设计助理',
    capability: '方案推敲与专业资料整理',
    gap: '施工图审签未接入',
    accent: '#d9b985',
    activity: '建筑设计师在工位绘图，随后沿过道查看部门索引',
  },
  {
    id: 'planning',
    name: '规划与景观所',
    plaque: '规划与景观所',
    nameEn: 'PLANNING & LANDSCAPE',
    roomCode: 'A-02',
    entry: 'canvas',
    entryLabel: '创意画板',
    capability: '总图与场地分析图推敲',
    gap: '日照、GIS 计算未接入',
    accent: '#a9b98f',
    activity: '规划师在沙盘上布置场地标记',
  },
  {
    id: 'structure',
    name: '结构设计所',
    plaque: '结构设计所',
    nameEn: 'STRUCTURAL ENGINEERING',
    roomCode: 'A-03',
    entry: 'drawing',
    entryLabel: '图纸阅览',
    capability: '本机 DXF 图纸查看',
    gap: '结构计算未接入',
    accent: '#b6b6b0',
    activity: '结构工程师对着构件图核对梁柱关系',
  },
  {
    id: 'mep',
    name: '机电设计所',
    plaque: '机电设计所',
    nameEn: 'MEP ENGINEERING',
    roomCode: 'B-01',
    entry: 'model',
    entryLabel: '模型阅览',
    capability: '三维模型与几何查看',
    gap: '机电计算与碰撞检查未接入',
    accent: '#9fb2bd',
    activity: '机电工程师在工位核对风管走向',
  },
  {
    id: 'interior',
    name: '室内设计所',
    plaque: '室内设计所',
    nameEn: 'INTERIOR DESIGN',
    roomCode: 'B-02',
    entry: 'interior',
    entryLabel: '空间设计',
    capability: '户型分析与室内三维方案',
    gap: '材料清单与造价未接入',
    accent: '#cba98d',
    activity: '室内设计师在材料板前比对样板',
  },
  {
    id: 'bim',
    name: 'BIM 与数字技术中心',
    plaque: 'BIM 数字技术中心',
    nameEn: 'BIM & DIGITAL',
    roomCode: 'B-03',
    entry: 'tools',
    entryLabel: '工具中心',
    capability: '连接 Revit、Rhino、Blender 等本机软件',
    gap: '模型协同与版本管理未接入',
    accent: '#8fb7aa',
    activity: '数字技术工程师在模型屏前检查构件',
  },
  {
    id: 'review',
    name: '总师办评审区',
    plaque: '总师办评审区',
    nameEn: 'CHIEF ARCHITECTS REVIEW',
    roomCode: 'R-00',
    entry: 'deliverables',
    entryLabel: '成果交付',
    capability: '方案文档与汇报整理',
    gap: '审签流程未接入',
    accent: '#e0c790',
    activity: '总师在评审桌前查看实体模型',
  },
];

export function departmentById(id: string): Department | undefined {
  return departments.find(item => item.id === id);
}

/** A task row supplied by the local task API. No value is fabricated here. */
export type HomeTask = {
  id: string;
  title: string;
  status: string;
  department?: string;
  sessionId?: string;
  detail?: string;
  /** 0..1 only when the backend reports real progress. */
  progress?: number;
  updatedAt?: string;
};

/** A delivered artifact supplied by the local deliverables API. */
export type HomeArtifact = {
  id: string;
  title: string;
  kind?: string;
  updatedAt?: string;
  sessionId?: string;
};

export const taskStatusLabel: Record<string, string> = {
  queued: '排队中',
  pending: '等待开始',
  awaiting_input: '等待你的确认',
  running: '执行中',
  streaming: '执行中',
  completed: '已完成',
  done: '已完成',
  failed: '失败',
  cancelled: '已停止',
  stopped: '已停止',
};
