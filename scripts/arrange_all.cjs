/**
 * Batch layout de canvases con elkjs — MISMA librería y config que el botón
 * "Autoarrange" del canvas (web-data-model-hub/src/features/modeler/lib/elkLayout.ts).
 * NO reinventa nada: reusa el Eclipse Layout Kernel. Lee un JSON de grafos
 * {saId: {nodes:[{id,w,h}], edges:[{id,source,target}]}} y escribe
 * {saId: {tableId:{x,y}}}. Los tamaños de nodo los estima arrange_all.py.
 *
 *   node arrange_all.cjs <in.json> <out.json>
 */
const fs = require('fs');
const ELK = require('/Users/carlosperez/Desktop/Projects/Agentes/GitHub/WebApp/MODELER/web-data-model-hub/node_modules/elkjs/lib/elk.bundled.js');

// Idéntica a elkLayout.ts (mantener en sync).
const opts = {
  'elk.algorithm': 'layered',
  'elk.direction': 'RIGHT',
  'elk.edgeRouting': 'ORTHOGONAL',
  'elk.separateConnectedComponents': 'true',
  'elk.spacing.componentComponent': '64',
  'elk.layered.spacing.nodeNodeBetweenLayers': '140',
  'elk.spacing.nodeNode': '72',
  'elk.spacing.edgeNode': '28',
  'elk.spacing.edgeEdge': '16',
  'elk.layered.spacing.edgeNodeBetweenLayers': '28',
  'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
  'elk.layered.nodePlacement.favorStraightEdges': 'true',
  'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
  'elk.layered.thoroughness': '10',
};

(async () => {
  const [, , inPath, outPath] = process.argv;
  const graphs = JSON.parse(fs.readFileSync(inPath, 'utf8'));
  const elk = new ELK();
  const out = {};
  const ids = Object.keys(graphs);
  let done = 0;
  for (const saId of ids) {
    const g = graphs[saId];
    const graph = {
      id: 'root',
      layoutOptions: opts,
      children: g.nodes.map((n) => ({ id: n.id, width: n.w, height: n.h })),
      edges: g.edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
    };
    const t0 = Date.now();
    try {
      const res = await elk.layout(graph);
      const pos = {};
      (res.children ?? []).forEach((c) => { pos[c.id] = { x: Math.round(c.x ?? 0), y: Math.round(c.y ?? 0) }; });
      out[saId] = pos;
    } catch (e) {
      console.error(`  [fail] ${saId}: ${e.message}`);
    }
    done++;
    if (g.nodes.length >= 60 || done % 25 === 0) {
      console.error(`  ${done}/${ids.length} · ${saId} (${g.nodes.length} nodos, ${g.edges.length} rel) ${Date.now() - t0}ms`);
    }
  }
  fs.writeFileSync(outPath, JSON.stringify(out));
  console.error(`arranged ${Object.keys(out).length}/${ids.length} canvases`);
})();
