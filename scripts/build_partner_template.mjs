import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputDir=`${root}/outputs/019ff196-7dcc-7242-8a16-64720facd020`;
const outputPath=`${outputDir}/合作方临床试验数据填写模板_v3.xlsx`;
const previewDir=`${root}/tmp/template_v3_previews`;
const C={navy:"#17365D",blue:"#1F4E78",teal:"#0F6B78",orange:"#C65911",yellow:"#FFF2CC",sample:"#DDEBF7",green:"#E2F0D9",white:"#FFFFFF",grid:"#D9E2F3",text:"#1F2937",muted:"#5B6573"};
const wb=Workbook.create();
const col=i=>{let n=i+1,s="";while(n){const r=(n-1)%26;s=String.fromCharCode(65+r)+s;n=Math.floor((n-1)/26);}return s;};

function addEntrySheet(name,title,columns,example,required=[],validations={}){
  const sh=wb.worksheets.add(name), last=col(columns.length-1), req=new Set(required);
  sh.showGridLines=false;
  sh.getRange(`A1:${last}1`).merge(); sh.getRange("A1").values=[[title]];
  sh.getRange(`A1:${last}1`).format={fill:C.navy,font:{bold:true,color:C.white,size:15},verticalAlignment:"center"};
  sh.getRange(`A2:${last}2`).merge(); sh.getRange("A2").values=[["蓝色行为示例，请删除或覆盖；黄色区域用于填写。所有子表均通过“合作方试验编号”关联试验主表。"]];
  sh.getRange(`A2:${last}2`).format={fill:C.green,font:{color:C.text},wrapText:true};
  sh.getRange(`A3:${last}3`).values=[columns]; sh.getRange(`A4:${last}4`).values=[example];
  columns.forEach((name,i)=>{const c=col(i);sh.getRange(`${c}3`).format={fill:req.has(name)?C.orange:C.blue,font:{bold:true,color:C.white},wrapText:true,horizontalAlignment:"center",verticalAlignment:"center",borders:{preset:"all",style:"thin",color:C.grid}};const wide=/名称|疾病|人群|标准|说明|摘要|地址|干预|链接|联系方式|备注|标题/.test(name);sh.getRange(`${c}:${c}`).format.columnWidth=wide?30:16;});
  sh.getRange(`A4:${last}4`).format={fill:C.sample,font:{color:C.muted},wrapText:true,verticalAlignment:"top",borders:{preset:"all",style:"thin",color:C.grid}};sh.getRange(`A4:${last}4`).format.rowHeight=62;
  sh.getRange(`A5:${last}204`).format={fill:C.yellow,font:{color:C.text},wrapText:true,verticalAlignment:"top",borders:{preset:"all",style:"thin",color:"#E7E6E6"}};
  for(const [header,values] of Object.entries(validations)){const i=columns.indexOf(header);if(i>=0){const c=col(i);sh.getRange(`${c}4:${c}204`).dataValidation={rule:{type:"list",values}};}}
  sh.freezePanes.freezeRows(3); sh.freezePanes.freezeColumns(1);
  const table=sh.tables.add(`A3:${last}204`,true,`${name.replace(/[^\w\u4e00-\u9fff]/g,"")}表`);table.showBandedRows=false;table.showFilterButton=true;
  return sh;
}

const intro=wb.worksheets.add("填写说明");intro.showGridLines=false;
intro.getRange("A1:H1").merge();intro.getRange("A1").values=[["中国临床试验合作方数据模板 · 第3版"]];intro.getRange("A1:H1").format={fill:C.navy,font:{bold:true,color:C.white,size:18}};
intro.getRange("A3:H3").merge();intro.getRange("A3").values=[["支持两种提交方式：①直接提交ChiCTR/WHO ICTRP导出的XML；②填写本工作簿。两种方式进入同一数据库结构。"]];intro.getRange("A3:H3").format={fill:C.green,font:{bold:true,color:C.text},wrapText:true};
intro.getRange("A5:B13").values=[
 ["规则","说明"],["一项试验一行","试验主表只放试验级字段；中心、队列、标准等一对多数据分别填写在子表。"],["关联键","所有子表都用稳定的“合作方试验编号”关联，不使用试验名称关联。"],["入排标准","每一行只填写一条完整医学条件，不要按页面宽度或视觉换行拆断句子。"],["结构化条件","患者字段、比较关系、比较值只有在可明确计算时填写；不确定就留空。"],["中心证据","国家级记录不能填写成具体招募中心；证据类型选择“仅国家记录”。"],["最少填写","只需完整填写试验主表必填列、至少一条入选标准和一条排除标准；其他子表按实际情况填写。"],["原始文件","如直接提交XML，不要求重复填写本模板；系统会保留原始XML用于审计。"],["患者信息","本文件只收集试验资料，不填写患者姓名、电话、证件号或病历。"]];
intro.getRange("A5:B5").format={fill:C.blue,font:{bold:true,color:C.white}};intro.getRange("A6:A13").format={fill:"#EAF2F8",font:{bold:true,color:C.navy}};intro.getRange("A5:B13").format.borders={preset:"all",style:"thin",color:C.grid};intro.getRange("B6:B13").format={wrapText:true};intro.getRange("A:A").format.columnWidth=18;intro.getRange("B:B").format.columnWidth=90;
intro.getRange("D5:F5").values=[["填写检查","当前值","目标"]];intro.getRange("D5:F5").format={fill:C.teal,font:{bold:true,color:C.white}};intro.getRange("D6:D8").values=[["试验数"],["入选标准数"],["排除标准数"]];intro.getRange("E6:E8").formulas=[["=COUNTA('试验主表'!G5:G204)"],["=COUNTIF('入排标准'!B5:B204,\"入选\")"],["=COUNTIF('入排标准'!B5:B204,\"排除\")"]];intro.getRange("F6:F8").values=[[">0"],[">0"],[">0"]];intro.getRange("D5:F8").format.borders={preset:"all",style:"thin",color:C.grid};intro.freezePanes.freezeRows(3);

const mainCols=["模板版本","合作机构编码","合作机构名称","合作状态","提交批次号","数据截至日期","合作方试验编号","主注册库","ChiCTR注册号","UTRN","方案编号","其他注册号","试验公开名称","试验英文名称","科学名称","招募状态","研究类型","研究设计","研究阶段","主要疾病或研究人群","疾病别名","病理类型","疾病分期或状态","关键分子标志物","干预概述","计划入组人数","计划入组说明","性别限制","最小年龄（岁）","最大年龄（岁）","最大ECOG评分","开始入组日期","计划结束日期","注册日期","最后更新日期","国家或地区","主办单位","组长单位","来源链接","最后核验日期","记录状态","研究摘要","数据备注"];
const mainExample=["中国临床试验合作方模板-第3版","示例机构","示例合作公司","合作中","示例批次-202608","2026-08-31","示例试验-001","ChiCTR","ChiCTR2600118646","","方案-001","","晚期实体瘤影像研究","Advanced solid tumor imaging study","Scientific title","招募中","干预性研究","单臂","I期","晚期实体瘤","结直肠癌、肾癌、肺癌、乳腺癌","","局部晚期、复发或转移","","研究药物SPECT/CT",32,"四个队列，每队列8人","不限",18,"",2,"2026-01-04","","2026-02-09","","中国","示例合作公司","示例医院","https://www.chictr.org.cn/showproj.html?proj=示例","2026-08-31","有效","只填写研究目的摘要，不做疗效分析","示例行"];
addEntrySheet("试验主表","试验主表：一项试验一行",mainCols,mainExample,["模板版本","合作机构编码","合作机构名称","合作状态","提交批次号","数据截至日期","合作方试验编号","试验公开名称","招募状态","主要疾病或研究人群","主办单位","来源链接","最后核验日期","记录状态"],{"合作状态":["拟合作","合作中","暂停合作","已终止合作"],"招募状态":["尚未开始","招募中","暂停招募","停止招募","已完成","未知"],"记录状态":["有效","停用"],"性别限制":["不限","男","女","Both","Male","Female"]});
addEntrySheet("注册号","注册号：一个注册号一行",["合作方试验编号","注册库","注册号","编号类型","是否主注册号","来源链接"],["示例试验-001","ChiCTR","ChiCTR2600118646","主注册号","是","https://www.chictr.org.cn/showproj.html?proj=示例"],["合作方试验编号","注册号"],{"是否主注册号":["是","否"],"编号类型":["主注册号","次级注册号","方案号","合作方编号"]});
addEntrySheet("研究中心","研究中心或国家记录：一条证据一行",["合作方试验编号","中心编号","中心名称","国家","省","市","区县","招募状态","主要研究者","联系方式","地址","证据类型","最后核验日期"],["示例试验-001","中心-001","示例医院","中国","陕西省","西安市","","招募中","","","","已核验中心","2026-08-31"],["合作方试验编号","中心名称","国家","证据类型"],{"证据类型":["已核验中心","合作方报告中心","仅国家记录"],"招募状态":["尚未开始","招募中","暂停招募","停止招募","已完成","未知"]});
addEntrySheet("队列与干预","队列与干预：一个队列一行",["合作方试验编号","队列编号","队列名称","队列状态","疾病范围","标志物范围","干预名称","干预类型","计划人数","队列说明"],["示例试验-001","COHORT-01","肺癌队列","招募中","局部晚期、复发或转移性非小细胞肺癌","","177Lu-R10306 SPECT/CT","影像/诊断",8,"完整保留队列范围"],["合作方试验编号","队列编号","队列名称","疾病范围"]);
addEntrySheet("入排标准","入排标准：一条完整医学条件一行",["合作方试验编号","标准类型","队列编号","标准编号","序号","类别","标准原文","英文原文","是否必需","患者字段","比较关系","比较值","单位"],["示例试验-001","入选","全部队列","入选-001",1,"年龄","年龄≥18岁","Age >= 18 years","是","年龄","大于等于",18,"年"],["合作方试验编号","标准类型","序号","标准原文"],{"标准类型":["入选","排除"],"是否必需":["是","否"],"类别":["疾病","病理","分期","生物标志物","年龄","性别","体能状态","既往治疗","治疗线数","器官功能","实验室","合并症","妊娠","其他"],"患者字段":["年龄","性别","体能状态","疾病","病理","分期","生物标志物","突变","既往治疗","治疗线数","合并症","妊娠"],"比较关系":["等于","不等于","属于","不属于","大于等于","小于等于","介于","包含","不包含","已提供","未提供"]});
addEntrySheet("联系人","联系人：一名联系人一行（可选）",["合作方试验编号","联系人类型","姓名","单位","国家","省","市","地址","电话","邮箱"],["示例试验-001","科学联系人","示例联系人","示例医院","中国","陕西省","西安市","","",""],[]);
addEntrySheet("结局指标","结局指标：一个指标一行（不参与患者排除）",["合作方试验编号","指标类型","序号","指标原文"],["示例试验-001","主要",1,"安全性指标"],[],{"指标类型":["主要","次要"]});
addEntrySheet("伦理审查","伦理审查：一次审查一行（不参与患者排除）",["合作方试验编号","审查状态","批准日期","伦理委员会","联系人","联系方式"],["示例试验-001","已批准","2025-11-24","示例伦理委员会","",""],[],{"审查状态":["已批准","待审查","豁免","未批准","未知"]});

const dict=wb.worksheets.add("字段字典");dict.showGridLines=false;dict.getRange("A1:F1").merge();dict.getRange("A1").values=[["字段字典与XML映射"]];dict.getRange("A1:F1").format={fill:C.navy,font:{bold:true,color:C.white,size:15}};
dict.getRange("A3:F3").values=[["数据库实体","模板工作表","关键字段","XML路径示例","是否用于匹配","说明"]];dict.getRange("A3:F3").format={fill:C.blue,font:{bold:true,color:C.white}};
dict.getRange("A4:F12").values=[
 ["trial","试验主表","注册号、标题、状态、疾病、日期","trial/main/*","是","试验级主记录"],["registry_id","注册号","注册库、注册号、主次标志","trial/main/trial_id; secondary_ids","用于去重","一个试验可有多个注册号"],["site/country","研究中心","中心、城市、国家、证据类型","trial/countries; MCP sites","位置辅助","仅国家记录不能冒充中心"],["cohort/intervention","队列与干预","队列疾病范围、标志物、干预","main/target_size; main/i_freetext","是","队列范围优先于试验总标题"],["criterion","入排标准","类型、队列、原文、可计算字段","trial/criteria/*","核心","排除优先流程的主要数据"],["contact","联系人","姓名、单位、联系方式","trial/contacts/contact","否","只用于后续联络，不发送给模型"],["outcome","结局指标","主要/次要指标原文","primary_outcome; secondary_outcome","否","保留审计，不做疗效分析"],["ethics","伦理审查","状态、日期、委员会","ethics_reviews/ethics_review","否","保留来源信息"],["raw_source","系统自动保存","原始XML或MCP JSON","完整trial节点","审计","合作方无需填写"]];dict.getRange("A3:F12").format.borders={preset:"all",style:"thin",color:C.grid};dict.getRange("A4:F12").format={wrapText:true,verticalAlignment:"top"};[18,18,34,38,16,46].forEach((w,i)=>dict.getRange(`${col(i)}:${col(i)}`).format.columnWidth=w);dict.freezePanes.freezeRows(3);

await fs.mkdir(outputDir,{recursive:true});await fs.mkdir(previewDir,{recursive:true});
const xlsx=await SpreadsheetFile.exportXlsx(wb);await xlsx.save(outputPath);
const verified=await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
for(const name of ["填写说明","试验主表","注册号","研究中心","队列与干预","入排标准","联系人","结局指标","伦理审查","字段字典"]){const range=name==="填写说明"?"A1:H13":name==="字段字典"?"A1:F12":"A1:M8";const check=await verified.inspect({kind:"table",sheetId:name,range,include:"values,formulas",tableMaxRows:8,tableMaxCols:15,tableMaxCellChars:100,maxChars:5000});console.log(`INSPECT ${name}\n${check.ndjson}`);const preview=await verified.render({sheetName:name,range,scale:0.75,format:"png"});await fs.writeFile(`${previewDir}/${name}.png`,new Uint8Array(await preview.arrayBuffer()));}
const errors=await verified.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",options:{useRegex:true,maxResults:100},summary:"公式错误扫描",maxChars:3000});console.log(`ERROR_SCAN\n${errors.ndjson}`);console.log(`OUTPUT ${outputPath}`);
