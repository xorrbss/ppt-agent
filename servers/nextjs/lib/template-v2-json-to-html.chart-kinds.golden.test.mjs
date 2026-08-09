// Characterization (golden-output) coverage for lib/template-v2-json-to-html.ts.
// Chart element: the Chart.js configuration embedded on the canvas.
//
// These assertions pin the renderer's CURRENT output byte for byte, quirks
// included, so that refactoring the module (for example splitting it) can be
// proven behavior-preserving. A diff here means the rendered HTML changed:
// do not relax an expectation to make new output pass.
import assert from "node:assert/strict";
import test from "node:test";

import {
  readChartConfig,
  renderElementHtml,
} from "./template-v2-json-to-html.test-harness.mjs";

test("chart config: bar", async () => {
  const html = await renderElementHtml({
    "type": "chart",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 640,
      "height": 360
    },
    "chartType": "bar",
    "categories": [
      "One",
      "Two"
    ],
    "series": [
      {
        "name": "Values",
        "values": [
          10,
          20
        ]
      }
    ]
  });

  assert.deepEqual(readChartConfig(html), {"type":"bar","data":{"labels":["One","Two"],"datasets":[{"label":"Values","data":[10,20],"backgroundColor":["#7F22FE","#7F22FE"],"borderColor":"#7F22FE","borderWidth":0,"borderSkipped":false,"fill":false,"maxBarThickness":62,"presentonBarRadius":{"horizontal":false,"radius":7},"pointBackgroundColor":["#7F22FE","#7F22FE"],"pointBorderColor":"#FFFFFF","pointBorderWidth":0,"pointRadius":0,"tension":0}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":9.504000000000001,"boxHeight":9.504000000000001,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":11.88,"weight":600},"padding":11.88,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":15.84,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":10.440000000000001,"horizontal":false,"position":"top"}},"scales":{"x":{"display":true,"border":{"color":"#98A2B3","display":true},"grid":{"color":"rgba(152, 162, 179, 0.25)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":11.88,"weight":600},"maxRotation":0,"autoSkip":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":11.88,"weight":700},"text":""},"type":"category"},"y":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":9.88,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":11.88,"weight":700},"text":""},"type":"linear"}}}});
});

test("chart config: horizontal_bar", async () => {
  const html = await renderElementHtml({
    "type": "chart",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 640,
      "height": 360
    },
    "chartType": "horizontalBar",
    "categories": [
      "One",
      "Two"
    ],
    "series": [
      {
        "name": "Values",
        "values": [
          10,
          20
        ]
      }
    ]
  });

  assert.deepEqual(readChartConfig(html), {"type":"bar","data":{"labels":["One","Two"],"datasets":[{"label":"Values","data":[10,20],"backgroundColor":["#7F22FE","#7F22FE"],"borderColor":"#7F22FE","borderWidth":0,"borderSkipped":false,"fill":false,"maxBarThickness":62,"presentonBarRadius":{"horizontal":true,"radius":7},"pointBackgroundColor":["#7F22FE","#7F22FE"],"pointBorderColor":"#FFFFFF","pointBorderWidth":0,"pointRadius":0,"tension":0}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"y","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":9.504000000000001,"boxHeight":9.504000000000001,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":11.88,"weight":600},"padding":11.88,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":15.84,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":10.440000000000001,"horizontal":true,"position":"top"}},"scales":{"x":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":9.88,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":11.88,"weight":700},"text":""},"type":"linear"},"y":{"display":true,"border":{"color":"#98A2B3","display":true},"grid":{"color":"rgba(152, 162, 179, 0.25)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":11.88,"weight":600},"maxRotation":0,"autoSkip":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":11.88,"weight":700},"text":""},"type":"category"}}}});
});

test("chart config: stacked_bar", async () => {
  const html = await renderElementHtml({
    "type": "chart",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 640,
      "height": 360
    },
    "chart_type": "stacked_bar",
    "categories": [
      "One",
      "Two"
    ],
    "series": [
      {
        "name": "A",
        "values": [
          1,
          2
        ]
      },
      {
        "name": "B",
        "values": [
          3,
          4
        ]
      }
    ]
  });

  assert.deepEqual(readChartConfig(html), {"type":"bar","data":{"labels":["One","Two"],"datasets":[{"label":"A","data":[1,2],"backgroundColor":"#7F22FE","borderColor":"#7F22FE","borderWidth":0,"borderRadius":7,"borderSkipped":"start","fill":false,"maxBarThickness":62,"pointBackgroundColor":"#7F22FE","pointBorderColor":"#FFFFFF","pointBorderWidth":0,"pointRadius":0,"tension":0},{"label":"B","data":[3,4],"backgroundColor":"#7F22FE","borderColor":"#7F22FE","borderWidth":0,"borderRadius":7,"borderSkipped":"start","fill":false,"maxBarThickness":62,"pointBackgroundColor":"#7F22FE","pointBorderColor":"#FFFFFF","pointBorderWidth":0,"pointRadius":0,"tension":0}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":9.504000000000001,"boxHeight":9.504000000000001,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":11.88,"weight":600},"padding":11.88,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":15.84,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":10.440000000000001,"horizontal":false,"position":"top"}},"scales":{"x":{"display":true,"border":{"color":"#98A2B3","display":true},"grid":{"color":"rgba(152, 162, 179, 0.25)","display":true,"drawTicks":true},"stacked":true,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":11.88,"weight":600},"maxRotation":0,"autoSkip":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":11.88,"weight":700},"text":""},"type":"category"},"y":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":true,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":9.88,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":11.88,"weight":700},"text":""},"type":"linear"}}}});
});

test("chart config: horizontal_stacked_bar", async () => {
  const html = await renderElementHtml({
    "type": "chart",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 320,
      "height": 180
    },
    "chart_type": "stacked-horizontal-bar",
    "categories": [
      "One"
    ],
    "series": [
      {
        "name": "A",
        "values": [
          1
        ]
      }
    ]
  });

  assert.deepEqual(readChartConfig(html), {"type":"bar","data":{"labels":["One"],"datasets":[{"label":"A","data":[1],"backgroundColor":["#7F22FE"],"borderColor":"#7F22FE","borderWidth":0,"borderRadius":7,"borderSkipped":"start","fill":false,"maxBarThickness":62,"pointBackgroundColor":["#7F22FE"],"pointBorderColor":"#FFFFFF","pointBorderWidth":0,"pointRadius":0,"tension":0}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"y","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":8,"boxHeight":8,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"padding":9,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":11,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":8,"horizontal":true,"position":"top"}},"scales":{"x":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":true,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":8,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"linear"},"y":{"display":true,"border":{"color":"#98A2B3","display":true},"grid":{"color":"rgba(152, 162, 179, 0.25)","display":true,"drawTicks":true},"stacked":true,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"maxRotation":0,"autoSkip":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"category"}}}});
});

test("chart config: line", async () => {
  const html = await renderElementHtml({
    "type": "chart",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 400,
      "height": 200
    },
    "chart_type": "line",
    "categories": [
      "a",
      "b",
      "c"
    ],
    "series": [
      {
        "name": "S",
        "values": [
          1,
          2,
          3
        ]
      }
    ]
  });

  assert.deepEqual(readChartConfig(html), {"type":"line","data":{"labels":["a","b","c"],"datasets":[{"label":"S","data":[1,2,3],"backgroundColor":"#7F22FE","borderColor":"#7F22FE","borderWidth":3,"fill":false,"maxBarThickness":62,"pointBackgroundColor":["#7F22FE","#7F22FE","#7F22FE"],"pointBorderColor":"#FFFFFF","pointBorderWidth":1.5,"pointRadius":3.5,"tension":0.35}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":8,"boxHeight":8,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"padding":9,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":11,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":8,"horizontal":false,"position":"top"}},"scales":{"x":{"display":true,"border":{"color":"#98A2B3","display":true},"grid":{"color":"rgba(152, 162, 179, 0.25)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"maxRotation":0,"autoSkip":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"category"},"y":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":8,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"linear"}}}});
});

test("chart config: area", async () => {
  const html = await renderElementHtml({
    "type": "chart",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 400,
      "height": 200
    },
    "chart_type": "area",
    "categories": [
      "a",
      "b"
    ],
    "series": [
      {
        "name": "S",
        "values": [
          1,
          2
        ]
      }
    ]
  });

  assert.deepEqual(readChartConfig(html), {"type":"line","data":{"labels":["a","b"],"datasets":[{"label":"S","data":[1,2],"backgroundColor":"rgba(127, 34, 254, 0.24)","borderColor":"#7F22FE","borderWidth":3,"fill":true,"maxBarThickness":62,"pointBackgroundColor":["#7F22FE","#7F22FE"],"pointBorderColor":"#FFFFFF","pointBorderWidth":1.5,"pointRadius":3.5,"tension":0.35}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":8,"boxHeight":8,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"padding":9,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":11,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":8,"horizontal":false,"position":"top"}},"scales":{"x":{"display":true,"border":{"color":"#98A2B3","display":true},"grid":{"color":"rgba(152, 162, 179, 0.25)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"maxRotation":0,"autoSkip":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"category"},"y":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":8,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"linear"}}}});
});

test("chart config: pie", async () => {
  const html = await renderElementHtml({
    "type": "chart",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 300,
      "height": 300
    },
    "chart_type": "pie",
    "categories": [
      "a",
      "b"
    ],
    "series": [
      {
        "name": "S",
        "values": [
          1,
          -2
        ]
      },
      {
        "name": "dropped",
        "values": [
          9
        ]
      }
    ]
  });

  assert.deepEqual(readChartConfig(html), {"type":"pie","data":{"labels":["a","b"],"datasets":[{"label":"S","data":[1,0],"backgroundColor":["#7F22FE","#7F22FE"],"borderColor":"#FFFFFF","borderWidth":1,"hoverOffset":0}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":16,"right":20,"bottom":12,"left":20}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":8,"boxHeight":8,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":9.9,"weight":600},"padding":9.9,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":13.2,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":8.700000000000001,"horizontal":false,"position":"top"}},"cutout":"0%"}});
});

test("chart config: donut", async () => {
  const html = await renderElementHtml({
    "type": "chart",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 300,
      "height": 300
    },
    "chart_type": "doughnut",
    "colors": [
      "#111111",
      "#222222"
    ],
    "categories": [
      "a",
      "b"
    ],
    "series": [
      {
        "name": "S",
        "values": [
          1,
          2
        ]
      }
    ]
  });

  assert.deepEqual(readChartConfig(html), {"type":"doughnut","data":{"labels":["a","b"],"datasets":[{"label":"S","data":[1,2],"backgroundColor":["#111111","#222222"],"borderColor":"#FFFFFF","borderWidth":1,"hoverOffset":0}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":16,"right":20,"bottom":12,"left":20}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":8,"boxHeight":8,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":9.9,"weight":600},"padding":9.9,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":13.2,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":8.700000000000001,"horizontal":false,"position":"top"}},"cutout":"58%"}});
});

test("chart config: polar_area", async () => {
  const html = await renderElementHtml({
    "type": "chart",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 300,
      "height": 300
    },
    "chart_type": "polar",
    "categories": [
      "a",
      "b"
    ],
    "series": [
      {
        "name": "S",
        "values": [
          1,
          2
        ]
      }
    ]
  });

  assert.deepEqual(readChartConfig(html), {"type":"polarArea","data":{"labels":["a","b"],"datasets":[{"label":"S","data":[1,2],"backgroundColor":["rgba(127, 34, 254, 0.78)","rgba(127, 34, 254, 0.78)"],"borderColor":["#7F22FE","#7F22FE"],"borderWidth":1}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":8,"boxHeight":8,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":9.9,"weight":600},"padding":9.9,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":13.2,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":8.700000000000001,"horizontal":false,"position":"top"}}}});
});

test("chart config: radar", async () => {
  const html = await renderElementHtml({
    "type": "chart",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 300,
      "height": 300
    },
    "chart_type": "radar",
    "categories": [
      "a",
      "b",
      "c"
    ],
    "series": [
      {
        "name": "S",
        "values": [
          1,
          2,
          3
        ]
      }
    ]
  });

  assert.deepEqual(readChartConfig(html), {"type":"radar","data":{"labels":["a","b","c"],"datasets":[{"label":"S","data":[1,2,3],"backgroundColor":["#7F22FE","#7F22FE","#7F22FE"],"borderColor":"#7F22FE","borderWidth":0,"fill":false,"maxBarThickness":62,"pointBackgroundColor":["#7F22FE","#7F22FE","#7F22FE"],"pointBorderColor":"#FFFFFF","pointBorderWidth":0,"pointRadius":0,"tension":0}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":8,"boxHeight":8,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":9.9,"weight":600},"padding":9.9,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":13.2,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":8.700000000000001,"horizontal":false,"position":"top"}},"scales":{"r":{"angleLines":{"color":"rgba(152, 162, 179, 0.35)","display":true},"beginAtZero":true,"grid":{"color":"rgba(152, 162, 179, 0.35)","display":true},"pointLabels":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":9.9,"weight":600}},"ticks":{"backdropColor":"transparent","color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":8.9},"presentonFormat":true}}}}});
});

test("chart config: scatter", async () => {
  const html = await renderElementHtml({
    "type": "chart",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 300,
      "height": 200
    },
    "chart_type": "scatter",
    "series": [
      {
        "name": "S",
        "values": [
          {
            "x": 1,
            "y": 2
          },
          {
            "x": 3,
            "y": 4
          }
        ]
      }
    ]
  });

  assert.deepEqual(readChartConfig(html), {"type":"scatter","data":{"labels":["Value 1","Value 2"],"datasets":[{"label":"S","data":[{"x":1,"y":2},{"x":3,"y":4}],"backgroundColor":["rgba(127, 34, 254, 0.78)","rgba(127, 34, 254, 0.78)"],"borderColor":["#7F22FE","#7F22FE"],"borderWidth":2,"pointRadius":4,"pointHoverRadius":4}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":8,"boxHeight":8,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"padding":9,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":11,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":8,"horizontal":false,"position":"top"}},"scales":{"x":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":8,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"linear"},"y":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":8,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"linear"}}}});
});

test("chart config: bubble", async () => {
  const html = await renderElementHtml({
    "type": "chart",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 300,
      "height": 200
    },
    "chart_type": "bubble",
    "series": [
      {
        "name": "S",
        "values": [
          {
            "x": 1,
            "y": 2,
            "r": 8
          },
          {
            "x": 3,
            "y": 4
          }
        ]
      },
      {
        "name": "T",
        "values": [
          {
            "x": 5,
            "y": 6
          }
        ]
      }
    ]
  });

  assert.deepEqual(readChartConfig(html), {"type":"bubble","data":{"labels":["Value 1","Value 2"],"datasets":[{"label":"S","data":[{"x":1,"y":2,"r":8},{"x":3,"y":4,"r":6}],"backgroundColor":["rgba(127, 34, 254, 0.78)"],"borderColor":["#7F22FE"],"borderWidth":2,"pointHoverRadius":4},{"label":"T","data":[{"x":5,"y":6,"r":6},{"x":2,"y":0,"r":6}],"backgroundColor":["rgba(127, 34, 254, 0.78)"],"borderColor":["#7F22FE"],"borderWidth":2,"pointHoverRadius":4}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":8,"boxHeight":8,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"padding":9,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":11,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":8,"horizontal":false,"position":"top"}},"scales":{"x":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":8,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"linear"},"y":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":8,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"linear"}}}});
});
