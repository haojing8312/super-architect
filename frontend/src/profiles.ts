import { MessageSquare, Box, DraftingCompass, Building2, Spline, Layers3, Monitor, Image, PenTool, LayoutTemplate, Presentation, BookOpen, Globe } from 'lucide-react';
export const profiles = [
 {id:'assistant',name:'设计助理',icon:MessageSquare,skills:[],heading:'把设计问题，放到一起思考',description:'从设计构思到成果交付，让想法变成作品。',examples:['查找建筑规范、设计资料和专业问答','辅助完成建模与出图','推敲设计方案和空间造型','分析图纸和参考图片']},
 {id:'sketchup',name:'SketchUp · 体块推敲',icon:Box,skills:['SketchUpMCP'],heading:'把建模交给我，你只需描述想法',description:'连接 SketchUp，在已有模型上继续深化。',examples:['生成建筑体块与方案模型','批量布置门窗、构件与组件','调整尺寸、材质与图层']},
 {id:'3dsMax',name:'3ds Max · 场景制作',icon:Layers3,skills:['3dsmaxMCP'],heading:'描述你的场景，我来完成建模',description:'连接 3ds Max，创建模型、材质和灯光。',examples:['搭建建筑、室内与产品模型','调整材质、灯光与摄像机','批量布置和修改场景对象']},
 {id:'revit',name:'Revit · 建筑信息',icon:Building2,skills:['RevitMCP'],heading:'描述你的建筑，我来完成建模',description:'连接 Revit MCP 插件，创建和管理建筑信息模型。',examples:['创建标高、轴网、墙体与楼板','布置门窗、梁柱与家具','检查模型与构件参数']},
 {id:'autocad',name:'CAD · 精准制图',icon:DraftingCompass,skills:['AutoCADMCP'],heading:'说出你要画的图，我来落笔',description:'支持 AutoCAD、中望、浩辰和 BricsCAD。',examples:['根据描述绘制平面、立面、剖面','绘制与编辑图层、标注和图块','按制图规范整理图纸样式']},
 {id:'rhino',name:'Rhino · 曲面造型',icon:Spline,skills:['RhinoMCP'],heading:'描述你的造型，我来建模',description:'连接 RhinoMCP，推敲曲面与参数化造型。',examples:['生成曲线、曲面与网格','推敲异形曲面与参数化造型','调整模型结构与精度']},
 {id:'blender',name:'Blender · 视觉表现',icon:Box,skills:['BlenderMCP'],heading:'描述你的场景，我来建模',description:'在 Blender 插件中启动 MCP Server 后开始。',examples:['创建建筑可视化和室内场景','调整材质、灯光与摄像机','用 Python 完成复杂操作']},
 {id:'grasshopper',name:'Grasshopper · 参数设计',icon:Spline,skills:['GrasshopperMCP'],heading:'描述你的需求，我来参数化建模',description:'连接 GrasshopperMCP 组件，搭建参数化算法。',examples:['搭建参数化流程与阵列','配置输入参数和数据树','调试组件错误并优化求解']},
 {id:'ppt',name:'演示提案',icon:Presentation,skills:['ppt-master'],heading:'告诉我汇报主题，我来做 PPT',description:'上传效果图、平面图和分析图，组织清晰的方案汇报。',examples:['根据项目资料生成演示文稿','统一字体、配色与版式','整理图文、图表和设计说明']},
 {id:'photoshop',name:'Photoshop · 图像后期',icon:Image,skills:['PhotoshopMCP'],heading:'说出你的修图需求，我来处理',description:'打开 Photoshop，在当前文档中处理图像。',examples:['制作分析图与效果图后期','调整图层、蒙版与滤镜','批量修图与格式转换']},
 {id:'illustrator',name:'Illustrator · 矢量表达',icon:PenTool,skills:['IllustratorMCP'],heading:'说出你的绘图需求，我来绘制',description:'打开 Illustrator，绘制图形与设计图示。',examples:['绘制矢量图形与图示','创建画板、图层与文字','路径布尔、描边与填色']},
 {id:'indesign',name:'InDesign · 版式编排',icon:LayoutTemplate,skills:['InDesignMCP'],heading:'说出你的排版需求，我来编排',description:'连接 InDesign，整理作品集和图文页面。',examples:['搭建页面与版式','管理页面、图层与主页','置入图片并导出 PDF']},
 {id:'webreport',name:'在线提案',icon:Globe,skills:['design-presentation-web'],heading:'把你的项目，变成可浏览的作品',description:'整理章节、图纸和说明，生成独立汇报网页。',examples:['创建建筑方案汇报网页','生成作品集长页','按建筑、室内、景观组织内容']},
 {id:'rag',name:'项目知识',icon:BookOpen,skills:[],heading:'上传资料后，向我提问',description:'检索自己的规范、文档和设计资料。',examples:['基于资料回答专业问题','总结与归纳上传的内容','查找规范中的关键条款']},
];
