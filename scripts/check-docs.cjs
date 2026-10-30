const fs = require('fs');
const path = require('path');
const docs = path.resolve(__dirname, '../docs');
const requirement = fs.readFileSync(path.join(docs,'01-requirements.md'),'utf8');
const stories = [...requirement.matchAll(/^\*\*(US-[A-Z]\d+(?:\.\d+)?) /gm)].map(x=>x[1]);
const duplicates = stories.filter((x,i)=>stories.indexOf(x)!==i);
const outdated = requirement.split('\n').filter(x=>/下一主题|尚待.*修订|为历史需求骨架|## 5. 待确认/.test(x));
const broken = [];
for (const file of ['README.md','00-project-charter.md','01-requirements.md','02-system-design.md','03-milestones.md','04-first-iteration.md','05-m1-local-guide.md','06-m2-tool-design.md','07-m3-agent-design.md','acceptance/M1-acceptance.md','acceptance/M2-T02a-verification.md','acceptance/M2-T02b1-verification.md','acceptance/M2-T02b2-verification.md','acceptance/M2-T03a-verification.md','acceptance/M2-T03b-verification.md','acceptance/M2-T04a-verification.md','acceptance/M2-T04b-verification.md','acceptance/M2-T05a-verification.md','acceptance/M2-T05b-verification.md','acceptance/M2-T05b2-verification.md','acceptance/M2-acceptance.md','acceptance/M3-T01-verification.md','../datasets/public-data-v2/README.md','../datasets/public-data-v1/README.md','appendices/open-source-verification.md','appendices/model-selection.md']) {
  const full = path.join(docs,file);
  for (const match of fs.readFileSync(full,'utf8').matchAll(/\]\(([^)]+)\)/g)) {
    const target = match[1];
    if (/^(?:https?:|[A-Z]:|#)/i.test(target)) continue;
    if (!fs.existsSync(path.resolve(path.dirname(full),target.split('#')[0]))) broken.push({file,target});
  }
}
const counts = Object.fromEntries([...new Set(stories.map(x=>x[3]))].map(group=>[group,stories.filter(x=>x[3]===group).length]));
const architecture=fs.readFileSync(path.join(docs,'02-system-design.md'),'utf8');
const architectureSections=[...architecture.matchAll(/^## (\d+)\./gm)].map(m=>Number(m[1]));
const architectureSequential=architectureSections.every((n,i)=>n===i+1);
const milestones=fs.readFileSync(path.join(docs,'03-milestones.md'),'utf8');
const mappedStories=[...milestones.matchAll(/^\| (US-[A-Z]\d+(?:\.\d+)?) \|/gm)].map(m=>m[1]);
const missingStories=stories.filter(id=>!mappedStories.includes(id));
const unknownStories=mappedStories.filter(id=>!stories.includes(id));
const repeatedMappings=mappedStories.filter((id,i)=>mappedStories.indexOf(id)!==i);
const requirementCases=[...requirement.matchAll(/^\| (AT-\d+) /gm)].map(m=>m[1]);
const mappedCases=[...milestones.matchAll(/^\| (AT-\d+) /gm)].map(m=>m[1]);
const missingCases=requirementCases.filter(id=>!mappedCases.includes(id));
const m2=fs.readFileSync(path.join(docs,'06-m2-tool-design.md'),'utf8');
const m2Sections=[...m2.matchAll(/^## (\d+)\./gm)].map(m=>Number(m[1]));
const m2Sequential=m2Sections.length>0&&m2Sections.every((n,i)=>n===i+1);
const m2Cases=[...m2.matchAll(/^\| (M2-AT\d+-[A-Z]\d+) \|/gm)].map(m=>m[1]);
const m2Duplicates=m2Cases.filter((id,i)=>m2Cases.indexOf(id)!==i);
const missingM2Groups=['M2-AT01','M2-AT02','M2-AT03','M2-AT04','M2-AT05'].filter(id=>!m2Cases.some(item=>item.startsWith(id+'-')));
const m3=fs.readFileSync(path.join(docs,'07-m3-agent-design.md'),'utf8');
const m3Sections=[...m3.matchAll(/^## (\d+)\./gm)].map(m=>Number(m[1]));
const m3Sequential=m3Sections.length>0&&m3Sections.every((n,i)=>n===i+1);
const m3Cases=[...m3.matchAll(/^\| (M3-T01-C\d+) \|/gm)].map(m=>m[1]);
const report = {m3_design_sections:m3Sections.length,m3_numbering_sequential:m3Sequential,m3_t01_contract_cases:m3Cases.length,story_sections:stories.length,counts,duplicates,outdated_placeholders:outdated,broken_local_links:broken,
  architecture_sections:architectureSections.length,architecture_numbering_sequential:architectureSequential,
  milestone_story_mappings:mappedStories.length,missing_milestone_stories:missingStories,unknown_milestone_stories:unknownStories,
  duplicate_milestone_mappings:repeatedMappings,mapped_end_to_end_cases:mappedCases.length,missing_end_to_end_cases:missingCases,
  m2_design_sections:m2Sections.length,m2_numbering_sequential:m2Sequential,
  m2_planned_cases:m2Cases.length,m2_duplicate_cases:m2Duplicates,missing_m2_acceptance_groups:missingM2Groups,
  lines:requirement.split('\n').length};
console.log(JSON.stringify(report,null,2));
if (!m3Sequential || m3Cases.length!==5 || duplicates.length || outdated.length || broken.length || !architectureSequential || missingStories.length || unknownStories.length || repeatedMappings.length || missingCases.length || !m2Sequential || m2Duplicates.length || missingM2Groups.length) process.exitCode=1;
