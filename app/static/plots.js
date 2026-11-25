import {element} from './client.js';

function block(target,className,content) {
  const node=element('div',content,{class:className});
  target.append(node);
  return node;
}
const note=(target,text)=>block(target,'chart-note',text);

export function visualize(report,selected) {
  const target=document.querySelector('#visual');
  target.replaceChildren();
  const measurement=report.cases?.find(row=>row.case===selected)?.measure;
  if (!measurement) {block(target,'chart-note','选择有计算值的分组');return;}
  const e=measurement.evidence;

  const matrix=block(target,'disclosure-cells');
  for(const [key,kind] of Object.entries(e.suppressed)){
    const cell=block(matrix,`cell-${kind.toLowerCase()}`,key);
    block(cell,'cell-state',e.exposed.includes(key)?'可反推':kind==='PRIMARY'?'初次遮蔽':'追加遮蔽');
  }
  if (!Object.keys(e.suppressed).length) note(target,'本切片无需遮蔽');
}
