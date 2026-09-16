import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputDir = `${root}/outputs/019ff196-7dcc-7242-8a16-64720facd020`;
const outputPath = `${outputDir}/合作方单试验填写表_v4.xlsx`;
const previewDir = `${root}/tmp/template_v4_previews`;
const C = {
  navy: "#17365D", blue: "#1F4E78", teal: "#0F6B78", orange: "#C65911",
  yellow: "#FFF2CC", green: "#E2F0D9", paleBlue: "#DDEBF7", white: "#FFFFFF",
  grid: "#D9E2F3", text: "#1F2937", muted: "#5B6573", paleGray: "#F3F6F9",
};

const wb = Workbook.create();
const sh = wb.worksheets.add("单试验填写表");
sh.showGridLines = false;
sh.freezePanes.freezeRows(5);
sh.getRange("A:A").format.columnWidth = 15;
sh.getRange("B:B").format.columnWidth = 17;
sh.getRange("C:C").format.columnWidth = 17;
sh.getRange("D:D").format.columnWidth = 17;
sh.getRange("E:E").format.columnWidth = 15;
sh.getRange("F:F").format.columnWidth = 17;
sh.getRange("G:G").format.columnWidth = 17;
sh.getRange("H:H").format.columnWidth = 17;

function mergeValue(range, value) {
  sh.getRange(range).merge();
  sh.getRange(range.split(":")[0]).values = [[value]];
}

function section(row, title, note) {
  mergeValue(`A${row}:H${row}`, title);
  sh.getRange(`A${row}:H${row}`).format = {
    fill: C.navy, font: { bold: true, color: C.white, size: 13 },
    verticalAlignment: "center", borders: { preset: "outside", style: "medium", color: C.navy },
  };
  sh.getRange(`A${row}:H${row}`).format.rowHeight = 26;
  if (note) {
    mergeValue(`A${row + 1}:H${row + 1}`, note);
    sh.getRange(`A${row + 1}:H${row + 1}`).format = { fill: C.paleBlue, font: { color: C.muted }, wrapText: true };
    sh.getRange(`A${row + 1}:H${row + 1}`).format.rowHeight = 27;
  }
}

function pairRow(row, leftLabel, rightLabel, options = {}) {
  const { leftRequired = false, rightRequired = false, leftValidation = null, rightValidation = null, height = 28 } = options;
  sh.getRange(`A${row}`).values = [[leftLabel]];
  sh.getRange(`E${row}`).values = [[rightLabel]];
  sh.getRange(`B${row}:D${row}`).merge();
  sh.getRange(`F${row}:H${row}`).merge();
  sh.getRange(`A${row}`).format = { fill: leftRequired ? C.orange : C.blue, font: { bold: true, color: C.white }, wrapText: true, verticalAlignment: "center" };
  sh.getRange(`E${row}`).format = { fill: rightRequired ? C.orange : C.blue, font: { bold: true, color: C.white }, wrapText: true, verticalAlignment: "center" };
  sh.getRange(`B${row}:D${row}`).format = { fill: C.yellow, font: { color: C.text }, wrapText: true, verticalAlignment: "center", borders: { preset: "outside", style: "thin", color: C.grid } };
  sh.getRange(`F${row}:H${row}`).format = { fill: C.yellow, font: { color: C.text }, wrapText: true, verticalAlignment: "center", borders: { preset: "outside", style: "thin", color: C.grid } };
  sh.getRange(`A${row}:H${row}`).format.rowHeight = height;
  if (leftValidation) sh.getRange(`B${row}`).dataValidation = { rule: { type: "list", values: leftValidation } };
  if (rightValidation) sh.getRange(`F${row}`).dataValidation = { rule: { type: "list", values: rightValidation } };
}

function fullRow(row, label, required = false, height = 42) {
  sh.getRange(`A${row}`).values = [[label]];
  sh.getRange(`B${row}:H${row}`).merge();
  sh.getRange(`A${row}`).format = { fill: required ? C.orange : C.blue, font: { bold: true, color: C.white }, wrapText: true, verticalAlignment: "center" };
  sh.getRange(`B${row}:H${row}`).format = { fill: C.yellow, font: { color: C.text }, wrapText: true, verticalAlignment: "top", borders: { preset: "outside", style: "thin", color: C.grid } };
  sh.getRange(`A${row}:H${row}`).format.rowHeight = height;
}

function entryTable(headerRow, headers, endRow, validations = {}) {
  sh.getRange(`A${headerRow}:H${headerRow}`).values = [headers];
  sh.getRange(`A${headerRow}:H${headerRow}`).format = {
    fill: C.teal, font: { bold: true, color: C.white }, wrapText: true,
    horizontalAlignment: "center", verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: C.grid },
  };
  sh.getRange(`A${headerRow}:H${headerRow}`).format.rowHeight = 34;
  sh.getRange(`A${headerRow + 1}:H${endRow}`).format = {
    fill: C.yellow, font: { color: C.text }, wrapText: true, verticalAlignment: "top",
    borders: { preset: "all", style: "thin", color: "#E7E6E6" },
  };
  sh.getRange(`A${headerRow + 1}:H${endRow}`).format.rowHeight = 34;
  headers.forEach((header, index) => {
    const values = validations[header];
    if (values) {
      const col = String.fromCharCode(65 + index);
      sh.getRange(`${col}${headerRow + 1}:${col}${endRow}`).dataValidation = { rule: { type: "list", values } };
    }
  });
}

mergeValue("A1:H1", "中国临床试验合作方 · 单试验填写表（第4版）");
sh.getRange("A1:H1").format = { fill: C.navy, font: { bold: true, color: C.white, size: 18 }, verticalAlignment: "center" };
sh.getRange("A1:H1").format.rowHeight = 36;
mergeValue("A2:H2", "一个文件只填写一项试验；所有内容都在本工作表。橙色标签为必填，黄色单元格为填写区。若直接提交 ChiCTR/WHO XML，可不重复填写本表。");
sh.getRange("A2:H2").format = { fill: C.green, font: { bold: true, color: C.text }, wrapText: true };
sh.getRange("A2:H2").format.rowHeight = 35;
mergeValue("A3:H3", "填写原则：标准原文必须是一条完整医学条件，不要按网页换行拆句；不知道或不适用时留空，不要猜测。国家记录不能冒充具体研究中心。");
sh.getRange("A3:H3").format = { fill: C.paleBlue, font: { color: C.muted }, wrapText: true };
sh.getRange("A3:H3").format.rowHeight = 32;
sh.getRange("A4:D4").merge(); sh.getRange("A4").values = [["建议文件名：合作机构_主注册号_数据日期.xlsx"]];
sh.getRange("E4:H4").merge(); sh.getRange("E4").values = [["本表不填写任何患者姓名、电话、证件号或病历信息"]];
sh.getRange("A4:H4").format = { fill: C.paleGray, font: { color: C.muted, italic: true }, wrapText: true };

section(6, "一、提交与合作机构", "用于管理合作关系和导入批次；合作状态只影响结果分组，不影响医学判断。");
pairRow(8, "模板版本*", "合作机构编码*", { leftRequired: true, rightRequired: true });
sh.getRange("B8").values = [["中国临床试验合作方单试验模板-第4版"]];
pairRow(9, "合作机构名称*", "合作状态*", { leftRequired: true, rightRequired: true, rightValidation: ["拟合作", "合作中", "暂停合作", "已终止合作"] });
pairRow(10, "提交批次号*", "数据截至日期*", { leftRequired: true, rightRequired: true });
pairRow(11, "合作方试验编号*", "记录状态*", { leftRequired: true, rightRequired: true, rightValidation: ["有效", "停用"] });

section(13, "二、试验身份与标题", "合作方试验编号在本公司内保持稳定；有注册号时请完整填写主注册号和来源链接。");
pairRow(15, "主注册库*", "主注册号*", { leftRequired: true, rightRequired: true });
pairRow(16, "ChiCTR注册号", "UTRN");
pairRow(17, "方案编号", "其他注册号");
fullRow(18, "试验公开名称*", true, 38);
fullRow(19, "试验英文名称", false, 38);
fullRow(20, "科学名称", false, 38);

section(22, "三、招募、疾病与干预", "这些字段用于剔除明显无关试验；疾病范围不清时应保留原始描述，不要自行缩窄。");
pairRow(24, "招募状态*", "研究类型*", { leftRequired: true, rightRequired: true, leftValidation: ["尚未开始", "招募中", "暂停招募", "停止招募", "已完成", "未知"], rightValidation: ["干预性研究", "观察性研究", "其他"] });
pairRow(25, "研究设计", "研究阶段");
fullRow(26, "主要疾病或研究人群*", true, 42);
pairRow(27, "疾病别名", "病理类型");
pairRow(28, "疾病分期或状态", "关键分子标志物");
fullRow(29, "干预概述", false, 42);
fullRow(30, "研究摘要", false, 55);

section(32, "四、基础入组范围、时间与来源", "年龄统一填写周岁；日期格式为 YYYY-MM-DD。来源链接应能追溯到注册页或合作方试验档案。");
pairRow(34, "计划入组人数", "计划入组说明");
pairRow(35, "性别限制", "最大ECOG评分", { leftValidation: ["不限", "男", "女"] });
pairRow(36, "最小年龄（岁）", "最大年龄（岁）");
pairRow(37, "开始入组日期", "计划结束日期");
pairRow(38, "注册日期", "最后更新日期");
pairRow(39, "国家或地区*", "最后核验日期*", { leftRequired: true, rightRequired: true });
pairRow(40, "主办单位*", "组长单位", { leftRequired: true });
fullRow(41, "来源链接*", true, 34);
fullRow(42, "数据备注", false, 42);

section(44, "五、其他注册号（可选）", "主注册号已在上方填写；这里仅补充次级注册号、方案号或其他注册库编号。");
entryTable(46, ["注册库", "注册号", "编号类型", "是否主注册号", "来源链接", "", "", ""], 50, { "编号类型": ["主注册号", "次级注册号", "方案号", "合作方编号"], "是否主注册号": ["是", "否"] });
sh.getRange("E46:H50").merge(true);

section(52, "六、队列与干预（可选，可填写多行）", "一行代表一个队列；若无分队列，可使用“全部队列”。");
entryTable(54, ["队列编号", "队列名称", "队列状态", "疾病范围", "标志物范围", "干预名称", "计划人数", "队列说明"], 60, { "队列状态": ["尚未开始", "招募中", "暂停招募", "停止招募", "已完成", "未知"] });
sh.getRange("D54:F60").format.columnWidth = 20;

section(62, "七、研究中心或国家记录（可选，可填写多行）", "地区按“国家｜省｜市”填写。只有明确的具名中心才能选择“已核验中心”。");
entryTable(64, ["中心编号", "中心名称", "地区（国家｜省｜市）", "招募状态", "主要研究者", "联系方式", "证据类型", "最后核验日期"], 70, { "招募状态": ["尚未开始", "招募中", "暂停招募", "停止招募", "已完成", "未知"], "证据类型": ["已核验中心", "合作方报告中心", "仅国家记录"] });

section(72, "八、入选与排除标准（核心，可填写多行）", "每行只填一条完整条件。结构化表达可留空，例如“年龄 >= 18 年”；系统仍会保留并分析标准原文。");
entryTable(74, ["标准类型*", "适用队列", "标准编号", "类别", "标准原文*", "是否必需", "结构化表达（可选）", "备注"], 103, { "标准类型*": ["入选", "排除"], "类别": ["疾病", "病理", "分期", "生物标志物", "年龄", "性别", "体能状态", "既往治疗", "治疗线数", "器官功能", "实验室", "合并症", "妊娠", "其他"], "是否必需": ["是", "否"] });
sh.getRange("E:E").format.columnWidth = 34;
sh.getRange("G:G").format.columnWidth = 22;
sh.getRange("A75:H103").format.rowHeight = 45;

section(105, "九、联系人（可选）", "仅用于试验联络，不发送给语言模型。");
entryTable(107, ["联系人类型", "姓名", "单位", "地区", "地址", "电话", "邮箱", "备注"], 111);

section(113, "十、结局指标（可选，不参与自动排除）", "用于保留完整试验档案；系统不进行疗效或风险评价。");
entryTable(115, ["指标类型", "序号", "指标原文", "", "", "", "", ""], 119, { "指标类型": ["主要", "次要"] });
sh.getRange("C115:H119").merge(true);

section(121, "十一、伦理审查（可选，不参与自动排除）", "填写已知信息即可，不要求为提交本表而额外收集。");
entryTable(123, ["审查状态", "批准日期", "伦理委员会", "联系人", "联系方式", "", "", ""], 127, { "审查状态": ["已批准", "待审查", "豁免", "未批准", "未知"] });
sh.getRange("E123:H127").merge(true);

mergeValue("A129:H129", "提交前快速检查");
sh.getRange("A129:H129").format = { fill: C.orange, font: { bold: true, color: C.white, size: 12 } };
mergeValue("A130:H132", "□ 主试验编号、主注册号、标题、招募状态、疾病范围、主办单位和来源链接已填写\n□ 至少填写一条入选标准和一条排除标准，且每条标准是完整句子\n□ 不确定内容已留空；没有填写患者隐私；如有 XML 已与本表一起提交");
sh.getRange("A130:H132").format = { fill: C.green, font: { color: C.text }, wrapText: true, verticalAlignment: "top", borders: { preset: "outside", style: "thin", color: C.grid } };
sh.getRange("A130:H132").format.rowHeight = 34;

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(outputPath);

const verified = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
for (const [name, range] of [["顶部与主信息", "A1:H42"], ["中心与标准", "A52:H103"], ["尾部可选信息", "A105:H132"]]) {
  const check = await verified.inspect({ kind: "table", sheetId: "单试验填写表", range, include: "values,formulas", tableMaxRows: 60, tableMaxCols: 8, tableMaxCellChars: 100, maxChars: 7000 });
  console.log(`INSPECT ${name}\n${check.ndjson}`);
  const preview = await verified.render({ sheetName: "单试验填写表", range, scale: 0.85, format: "png" });
  await fs.writeFile(`${previewDir}/${name}.png`, new Uint8Array(await preview.arrayBuffer()));
}
const errors = await verified.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, summary: "公式错误扫描", maxChars: 3000 });
console.log(`ERROR_SCAN\n${errors.ndjson}`);
console.log(`OUTPUT ${outputPath}`);
