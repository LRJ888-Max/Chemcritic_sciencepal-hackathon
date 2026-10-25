"use strict";
const $ = id => document.getElementById(id);
const form = $("experiment-form");
const field = name => form.elements.namedItem(name);
const assays = {unknown:"未知",isolated:"分离收率",gc:"GC收率",nmr:"NMR收率",lc:"LC收率",conversion:"转化率"};
const goals = {below_target:"未达到用户目标",met:"已达到用户目标",unavailable:"暂不能比较用户目标"};
const productLabels = {user_report_only:"用户报告结构匹配，系统未独立审核谱图或纯度。",missing:"未提供结构表征证据。",contradictory:"记录中存在不支持目标产物的表征信息，请先核查。",human_review_recorded:"已记录人工审核信息，系统未独立核验审核真实性。"};
const reasonLabels = {assay_mismatch:"结果口径不同，禁止直接比较数值。",assay_unknown:"结果口径未知，不能比较数值。",comparability_policy_pending:"同口径仍需核查条件；可比性规则未冻结，暂不进行数值比较。",mvp_numeric_disabled:"依B v0.2，MVP文献数值比较关闭；同口径也不能放行。"};
let bootstrap, revision=0, evaluated=null, busy=false;

for (const select of document.querySelectorAll(".assay-options")) {
  for (const [value,label] of Object.entries(assays)) select.add(new Option(label,value));
}
function message(text) { $("message").textContent=text; $("message").hidden=!text; }
function markDirty() {
  revision++; $("save").disabled=true; $("download").disabled=true;
  $("stale").hidden=!evaluated; $("saved-info").textContent="";
  if (evaluated) $("result-state").textContent="输入已修改";
}
function syncGoal() {
  const active=field("has_goal").checked;
  $("goal-fields").hidden=!active;
  field("minimum_yield_pct").required=active;
  field("minimum_yield_pct").disabled=!active;
  field("goal_yield_type").disabled=!active;
}
function fill(data,sourceText) {
  form.reset();
  const e=data,c=e.conditions||{},r=e.results||{},g=e.user_goal;
  field("experiment_id").value=e.experiment_id;
  field("simulated").value=String(e.simulated);
  field("substrate_1").value=e.reaction.substrates[0]; field("substrate_2").value=e.reaction.substrates[1];
  field("target_product").value=e.reaction.target_product;
  for (const key of ["catalyst","catalyst_loading_mol_pct","catalyst_loading_basis","solvent","temperature_C","time_h","base_status","base_name","base_equivalents","scale_mmol","solvent_volume_ml","partner_equivalents","atmosphere"]) field(key).value=c[key]??(key==="base_status"?"unknown":"");
  for (const key of ["yield_pct","yield_type","yield_basis","repeat_attempts"]) field(key).value=r[key]??(key==="yield_type"?"unknown":"");
  const evidence=r.characterization||[];
  field("evidence").value=!evidence.length?"missing":evidence.some(item=>!item.supports_target)?"contradictory":"user_report_only";
  field("method").value=evidence[0]?.method??"";
  field("has_goal").checked=Boolean(g&&g.minimum_yield_pct!=null);
  field("minimum_yield_pct").value=g?.minimum_yield_pct??"";
  field("goal_yield_type").value=g?.yield_type??"unknown";
  $("source-text").value=sourceText||""; $("source-file").value="";
  $("source-name").textContent=sourceText?"已载入SciencePal原始JSON；可展开查看。":"当前不提供先例。";
  syncGoal(); markDirty();
}
function readExperiment() {
  const text=name=>field(name).value.trim()||null;
  const number=name=>field(name).value===""?null:Number(field(name).value);
  const conditions={};
  for (const key of ["catalyst","catalyst_loading_basis","solvent","base_status","base_name","atmosphere"]) conditions[key]=text(key);
  for (const key of ["catalyst_loading_mol_pct","temperature_C","time_h","base_equivalents","scale_mmol","solvent_volume_ml","partner_equivalents"]) conditions[key]=number(key);
  const evidence=field("evidence").value;
  if(evidence!=="missing"&&!text("method")) throw new Error("请选择表征方法，或将结构证据改为未提供。");
  return {schema_version:"chemcritic.experiment.v0.1",experiment_id:text("experiment_id"),simulated:field("simulated").value==="true",
    reaction:{name:"Suzuki-Miyaura",substrates:[text("substrate_1"),text("substrate_2")],target_product:text("target_product")},conditions,
    results:{yield_pct:number("yield_pct"),yield_type:text("yield_type"),yield_basis:text("yield_basis"),repeat_attempts:number("repeat_attempts"),
      characterization:evidence==="missing"?[]:[{method:text("method"),supports_target:evidence!=="contradictory",provenance:"user_report",raw_spectrum_provided:false}]},
    user_goal:field("has_goal").checked?{minimum_yield_pct:number("minimum_yield_pct"),yield_type:text("goal_yield_type")}:null};
}
function payload() {return {experiment:readExperiment(),sciencepal_text:$("source-text").value};}
async function api(path,body) {
  const response=await fetch(path,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok) throw new Error([data.error,data.field,data.message].filter(Boolean).join(" · "));
  return data;
}
function appendText(parent,tag,text,className) {
  const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;parent.append(node);return node;
}
function render(result) {
  $("empty-result").hidden=true;$("result").hidden=false;
  $("stale").hidden=true;$("simulation-banner").hidden=!result.simulated;
  $("result-state").textContent=result.status==="S3"?"S3 · 证据不足":"评估完成 · 分维度报告";
  const goal=result.goal_comparison;
  $("goal-title").textContent=goals[goal.state];
  const noGoal={goal_not_set:"用户未设目标，系统不以文献替用户定义成功。",outcome_missing:"缺少实测结果。",assay_unknown:"实测或目标口径未知。",assay_mismatch:"实测与目标口径不同，不能直接判断。",evidence_gap:"结构证据缺失或矛盾，依B v0.2先补证据，暂不输出数值结论。"};
  $("goal-description").textContent=goal.state==="unavailable"?noGoal[goal.reason]:goal.gap_pp>0?`按用户所报数据，距目标差${goal.gap_pp}个百分点。`:`按用户所报数据，${goal.gap_pp===0?"恰好达到目标":"超过目标"+Math.abs(goal.gap_pp)+"个百分点"}。`;
  $("next-checks").replaceChildren();
  if(!result.next_checks?.length)appendText($("next-checks"),"p",result.next_checks?"当前规则未触发额外优先行动；不代表科学证据已全部核验。":"旧版历史快照未生成优先行动；修改后重新评估可使用新版规则。","hint");
  for(const action of result.next_checks||[]){
    const card=appendText($("next-checks"),"div","","action-card");
    appendText(card,"h4",`${action.priority}. ${action.title}`);appendText(card,"p",action.why);
    const list=appendText(card,"ul","");for(const material of action.requested_materials)appendText(list,"li",material);
    appendText(card,"p","补齐后："+action.expected_resolution);appendText(card,"p",action.limits,"hint");
    if(action.related_sources.length){appendText(card,"p","相关条件记录（用于核查字段，不是原因证据）：","hint");
      for(const source of action.related_sources){const p=appendText(card,"p",""),link=appendText(p,"a",source.record_id+" · "+source.locator);const url=new URL(source.url);if(["http:","https:"].includes(url.protocol)){link.href=url.href;link.target="_blank";link.rel="noopener noreferrer";}}
    }
  }
  $("product-evidence").textContent=productLabels[result.product_evidence.state];
  const retrieval=result.retrieval;
  const modeLabels={local_library_exact_pair:"团队本地库 · 完整底物对匹配",sciencepal_file_import:"SciencePal文件导入",provided_records:"用户提供先例"};
  $("retrieval-summary").textContent=`${modeLabels[retrieval.mode]||retrieval.mode} · ${retrieval.record_count}条结果记录 · ${retrieval.independent_publication_count??"未统计"}篇可识别DOI的论文。未进行实时检索。`;
  $("sources").replaceChildren();
  if(!result.precedent_comparisons.length) appendText($("sources"),"p",retrieval.no_match?"本地案例库未命中该底物对；不代表公开文献不存在先例。":"未提供文献先例；不表示不存在相关文献。","hint");
  for(const source of result.precedent_comparisons) {
    const card=appendText($("sources"),"div","","source-card");
    appendText(card,"p",source.record_id,"label");
    appendText(card,"p",reasonLabels[source.reason]||source.reason);
    appendText(card,"p",source.locator,"hint");
    const link=appendText(card,"a","打开文献原文 ↗");
    const url=new URL(source.url);
    if(["http:","https:"].includes(url.protocol)){link.href=url.href;link.target="_blank";link.rel="noopener noreferrer";}
    for(const [index,address] of (source.source_links||[]).entries()){
      if(address===source.url)continue;
      const parsed=new URL(address);if(!["http:","https:"].includes(parsed.protocol))continue;
      const p=appendText(card,"p",""),extra=appendText(p,"a",`来源入口${index+1}（原文 / SI） ↗`);
      extra.href=parsed.href;extra.target="_blank";extra.rel="noopener noreferrer";
    }
    appendText(card,"p",source.provenance==="team_workbook_report_unverified_locally"?"团队工作簿先例 · 本工程未独立核验原文":"SciencePal文件报告 · 原文与工具日志未独立核验","hint");
    for(const warning of source.import_metadata?.warnings||[])appendText(card,"p",warning,"hint");
    if(source.reported_outcome){const outcome=source.reported_outcome;
      appendText(card,"p",`文献报告值：${outcome.value_pct??"未知"}${outcome.value_pct==null?"":"%"}；${outcome.original_metric||assays[outcome.yield_type]||"口径未知"}。仅展示来源报告，不与本次结果作高低判断。`);
    }
    if(source.field_comparisons?.length){
      const details=document.createElement("details");card.append(details);appendText(details,"summary","查看逐字段差异（相同 / 不同 / 未知）");
      const scroll=appendText(details,"div","","comparison-scroll"),table=appendText(scroll,"table","","comparison-table");
      const head=appendText(table,"thead",""),headRow=appendText(head,"tr","");for(const label of ["维度","本次记录","文献记录","关系"])appendText(headRow,"th",label);
      const body=appendText(table,"tbody","");
      const display=value=>value==null?"未知":Array.isArray(value)?value.map(v=>v??"未知").join(" + "):({present:"已加入",absent:"明确未加入",unknown:"未知"}[value]||assays[value]||String(value));
      for(const item of source.field_comparisons){const row=appendText(body,"tr","");for(const value of [item.label,display(item.actual),display(item.precedent),{same:"相同",different:"不同",unknown:"未知"}[item.state]])appendText(row,"td",value);}
      appendText(details,"p","关系仅按记录值及少量明确别名对齐，不代表化学体系等价、反应活性相同或因果解释。","hint");
    }
    if(Object.keys(source.field_evidence||{}).length){const details=document.createElement("details");card.append(details);appendText(details,"summary","查看逐字段来源定位");appendText(details,"pre",JSON.stringify(source.field_evidence,null,2));}
    for(const limit of source.limitations||[])appendText(card,"p",limit,"hint");
  }
  const boundaries={causal_attribution:"不能据此确定未达目标的具体原因。",precedent_numeric:"当前不进行文献收率高低或优劣判断。",repeatability:"当前不评价实验重复性；重复次数本身不足以证明稳定。",target_comparison:"用户目标比较暂不可用。"};
  $("boundaries").replaceChildren();for(const key of result.blocked_dimensions)appendText($("boundaries"),"li",boundaries[key]||key);
  $("policy-note").textContent=`B v0.2：MVP文献数值比较关闭。${(result.pending_decisions||[]).join(" ")} 当前未启用LLM或原因诊断。`;
  $("followups").replaceChildren();for(const followup of result.followups)appendText($("followups"),"li",followup.question);
  if(!result.followups.length)appendText($("followups"),"li","当前规则未发现额外必问字段；不代表化学记录已完整核验。");
  $("raw-result").textContent=JSON.stringify(result,null,2);$("save").disabled=false;$("download").disabled=false;
}
form.addEventListener("input",event=>{if(event.target.id!=="source-file"){syncGoal();markDirty();}});
form.addEventListener("change",event=>{if(event.target.id!=="source-file"){syncGoal();markDirty();}});
$("source-text").addEventListener("input",()=>{$("source-name").textContent="已编辑原始JSON，需重新复盘。";});
$("source-file").addEventListener("change",async()=>{
  const file=$("source-file").files[0];if(!file)return;markDirty();const version=revision;
  try {if(file.size>1024*1024)throw new Error("先例文件不能超过1MB。");const text=await file.text();JSON.parse(text.replace(/^\uFEFF/,""));if(revision!==version)return;$("source-text").value=text;$("source-name").textContent="已选择："+file.name;message("");}
  catch(error){message(error.message);$("source-file").value="";}
});
$("clear-source").addEventListener("click",()=>{$("source-text").value="";$("source-file").value="";$("source-name").textContent="当前不提供先例。";markDirty();});
$("load-library").addEventListener("click",async()=>{
  if(busy)return;const version=revision;$("load-library").disabled=true;
  try{const library=await api("/api/library");if(revision!==version){message("输入已变化，请再次载入先例库。");return;}$("source-text").value=JSON.stringify(library);$("source-file").value="";$("source-name").textContent=`已载入团队库：${library.metadata.record_count}条结果记录、${library.metadata.independent_publication_count}篇论文。评估时按底物对匹配。`;markDirty();message("");}catch(error){message(error.message);}finally{$("load-library").disabled=false;}
});
$("load-example").addEventListener("click",()=>{if(bootstrap){fill(bootstrap.experiment,bootstrap.sciencepal_text);message("已载入模拟示例。修改后点击开始复盘。");}});
form.addEventListener("submit",async event=>{
  event.preventDefault();if(busy)return;busy=true;$("evaluate").disabled=true;$("save").disabled=true;$("download").disabled=true;message("正在评估…");
  const version=revision;
  try {const input=payload();const {result}=await api("/api/review",input);if(version!==revision){message("评估期间输入已修改，请重新复盘。");return;}evaluated={payload:input,result,revision:version};render(result);$("saved-info").textContent="尚未保存";message("");}
  catch(error){message(error.message);$("stale").hidden=!evaluated;}
  finally{busy=false;$("evaluate").disabled=false;}
});
$("save").addEventListener("click",async()=>{
  if(!evaluated||evaluated.revision!==revision||busy)return;busy=true;$("save").disabled=true;
  const version=revision;
  try{const record=await api("/api/reviews",evaluated.payload);if(version===revision)$("saved-info").textContent="已保存 · "+new Date(record.created_at).toLocaleString()+" · "+record.id;await loadHistory();}
  catch(error){message(error.message);if(version===revision)$("save").disabled=false;}
  finally{busy=false;}
});
$("download").addEventListener("click",()=>{
  if(!evaluated||evaluated.revision!==revision)return;
  const blob=new Blob([JSON.stringify({...evaluated.payload,result:evaluated.result},null,2)],{type:"application/json;charset=utf-8"});
  const url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download="chemcritic-review.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
async function loadHistory() {
  const records=await api("/api/history");$("history").replaceChildren();
  if(!records.length)appendText($("history"),"p","还没有保存记录。完成复盘后点击保存。","hint");
  for(const record of records){const row=appendText($("history"),"div","","history-row"),info=appendText(row,"div","");
    appendText(info,"strong",record.display_title||record.experiment_id);
    appendText(info,"small",`实验编号：${record.experiment_id}`);
    appendText(info,"small",`${record.simulated?"模拟":"真实记录"} · ${new Date(record.created_at).toLocaleString()} · ${goals[record.goal_state]||record.goal_state}`);
    const button=appendText(row,"button","打开","button secondary");button.addEventListener("click",async()=>{if(busy)return;
      try{const recordData=await api("/api/history/"+record.id);fill(recordData.experiment,recordData.sciencepal_text);evaluated={payload:{experiment:recordData.experiment,sciencepal_text:recordData.sciencepal_text},result:recordData.result,revision};render(recordData.result);$("save").disabled=true;$("saved-info").textContent="历史快照 · "+new Date(recordData.created_at).toLocaleString()+" · "+recordData.id;message("已打开历史快照。修改后重新评估，可另存新记录。");$("result-title").scrollIntoView({behavior:"smooth"});}catch(error){message(error.message);}});
  }
}
$("refresh-history").addEventListener("click",()=>loadHistory().catch(error=>message(error.message)));
(async()=>{try{bootstrap=await api("/api/bootstrap");fill(bootstrap.experiment,bootstrap.sciencepal_text);await loadHistory();}catch(error){message("初始化失败："+error.message);}})();
