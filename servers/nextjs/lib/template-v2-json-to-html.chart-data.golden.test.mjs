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

test("chart config: unknown kind falls back to bar", async () => {
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
    "chart_type": "sunburst",
    "categories": [
      "a"
    ],
    "series": [
      {
        "name": "S",
        "values": [
          1
        ]
      }
    ]
  });

  assert.deepEqual(readChartConfig(html), {"type":"bar","data":{"labels":["a"],"datasets":[{"label":"S","data":[1],"backgroundColor":["#7F22FE"],"borderColor":"#7F22FE","borderWidth":0,"borderSkipped":false,"fill":false,"maxBarThickness":62,"presentonBarRadius":{"horizontal":false,"radius":7},"pointBackgroundColor":["#7F22FE"],"pointBorderColor":"#FFFFFF","pointBorderWidth":0,"pointRadius":0,"tension":0}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":8,"boxHeight":8,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"padding":9,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":11,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":8,"horizontal":false,"position":"top"}},"scales":{"x":{"display":true,"border":{"color":"#98A2B3","display":true},"grid":{"color":"rgba(152, 162, 179, 0.25)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"maxRotation":0,"autoSkip":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"category"},"y":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":8,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"linear"}}}});
});

test("chart config: no series or data", async () => {
  const html = await renderElementHtml({
    "type": "chart",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 300,
      "height": 200
    }
  });

  assert.deepEqual(readChartConfig(html), {"type":"bar","data":{"labels":["Value 1"],"datasets":[{"label":"Series 1","data":[0],"backgroundColor":["#7F22FE"],"borderColor":"#7F22FE","borderWidth":0,"borderSkipped":false,"fill":false,"maxBarThickness":62,"presentonBarRadius":{"horizontal":false,"radius":7},"pointBackgroundColor":["#7F22FE"],"pointBorderColor":"#FFFFFF","pointBorderWidth":0,"pointRadius":0,"tension":0}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":false,"position":"bottom","labels":{"boxWidth":8,"boxHeight":8,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"padding":9,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":11,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":8,"horizontal":false,"position":"top"}},"scales":{"x":{"display":true,"border":{"color":"#98A2B3","display":true},"grid":{"color":"rgba(152, 162, 179, 0.25)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"maxRotation":0,"autoSkip":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"category"},"y":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":8,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"linear"}}}});
});

test("chart config: data points supply labels and colours", async () => {
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
    "chart_type": "bar",
    "title": "**Revenue**\nsecond line",
    "data": [
      {
        "label": "alpha",
        "value": 5,
        "color": "FF0000"
      },
      {
        "label": "beta",
        "value": 6
      }
    ]
  });

  assert.deepEqual(readChartConfig(html), {"type":"bar","data":{"labels":["alpha","beta"],"datasets":[{"label":"**Revenue**\nsecond line","data":[5,6],"backgroundColor":["#FF0000","#FF0000"],"borderColor":"#FF0000","borderWidth":0,"borderSkipped":false,"fill":false,"maxBarThickness":62,"presentonBarRadius":{"horizontal":false,"radius":7},"pointBackgroundColor":["#FF0000","#FF0000"],"pointBorderColor":"#FFFFFF","pointBorderWidth":0,"pointRadius":0,"tension":0}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":8,"boxHeight":8,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"padding":9,"usePointStyle":true}},"title":{"display":true,"text":["Revenue","second line"],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":11,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":8,"horizontal":false,"position":"top"}},"scales":{"x":{"display":true,"border":{"color":"#98A2B3","display":true},"grid":{"color":"rgba(152, 162, 179, 0.25)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"maxRotation":0,"autoSkip":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"category"},"y":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":8,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"linear"}}}});
});

test("chart config: axis, grid, legend and data label toggles", async () => {
  const html = await renderElementHtml({
    "type": "chart",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 800,
      "height": 500
    },
    "chart_type": "bar",
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
    ],
    "legend": false,
    "data_labels": "mid",
    "x_axis": false,
    "y_axis_grid": false,
    "x_axis_title": "X title",
    "yAxisTitle": "Y title",
    "axisColor": "334455",
    "gridColor": "#AABBCC",
    "textColor": "#123456",
    "titleColor": "#654321",
    "legendColor": "#ABCDEF",
    "title": "Titled"
  });

  assert.deepEqual(readChartConfig(html), {"type":"bar","data":{"labels":["a","b"],"datasets":[{"label":"S","data":[1,2],"backgroundColor":["#7F22FE","#7F22FE"],"borderColor":"#7F22FE","borderWidth":0,"borderSkipped":false,"fill":false,"maxBarThickness":62,"presentonBarRadius":{"horizontal":false,"radius":7},"pointBackgroundColor":["#7F22FE","#7F22FE"],"pointBorderColor":"#FFFFFF","pointBorderWidth":0,"pointRadius":0,"tension":0}]},"options":{"color":"#123456","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":false,"position":"bottom","labels":{"boxWidth":13.200000000000001,"boxHeight":13.200000000000001,"color":"#ABCDEF","font":{"family":"Inter, Arial, sans-serif","size":16.5,"weight":600},"padding":16.5,"usePointStyle":true}},"title":{"display":true,"text":["Titled"],"color":"#654321","font":{"family":"Inter, Arial, sans-serif","size":22,"weight":"700"},"padding":{"bottom":17.6,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":true,"color":"#123456","fontFamily":"Inter, Arial, sans-serif","fontSize":14.5,"horizontal":false,"position":"mid"}},"scales":{"x":{"display":false,"border":{"color":"#334455","display":false},"grid":{"color":"rgba(170, 187, 204, 0)","display":false,"drawTicks":false},"stacked":false,"ticks":{"color":"#334455","display":false,"font":{"family":"Inter, Arial, sans-serif","size":16.5,"weight":600},"maxRotation":0,"autoSkip":true},"title":{"color":"#334455","display":false,"font":{"family":"Inter, Arial, sans-serif","size":16.5,"weight":700},"text":"X title"},"type":"category"},"y":{"beginAtZero":true,"display":true,"border":{"color":"#334455","display":true},"grace":"8%","grid":{"color":"rgba(170, 187, 204, 0.35)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#334455","display":true,"font":{"family":"Inter, Arial, sans-serif","size":14.5,"weight":600},"presentonFormat":true},"title":{"color":"#334455","display":true,"font":{"family":"Inter, Arial, sans-serif","size":16.5,"weight":700},"text":"Y title"},"type":"linear"}}}});
});

test("chart config: legacy series colours and boolean data labels", async () => {
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
    "chart_type": "line",
    "categories": [
      "a",
      "b"
    ],
    "series_colors": [
      "#0F0F0F"
    ],
    "series": [
      {
        "name": "S",
        "values": [
          1,
          2
        ]
      }
    ],
    "dataLabels": true
  });

  assert.deepEqual(readChartConfig(html), {"type":"line","data":{"labels":["a","b"],"datasets":[{"label":"S","data":[1,2],"backgroundColor":"#0F0F0F","borderColor":"#0F0F0F","borderWidth":3,"fill":false,"maxBarThickness":62,"pointBackgroundColor":["#0F0F0F","#0F0F0F"],"pointBorderColor":"#FFFFFF","pointBorderWidth":1.5,"pointRadius":3.5,"tension":0.35}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":8,"boxHeight":8,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"padding":9,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":11,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":true,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":8,"horizontal":false,"position":"top"}},"scales":{"x":{"display":true,"border":{"color":"#98A2B3","display":true},"grid":{"color":"rgba(152, 162, 179, 0.25)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"maxRotation":0,"autoSkip":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"category"},"y":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":8,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"linear"}}}});
});

test("chart config: values longer than the category list are padded and capped", async () => {
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
    "chart_type": "bar",
    "categories": [
      "only"
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

  assert.deepEqual(readChartConfig(html), {"type":"bar","data":{"labels":["only","Value 2","Value 3"],"datasets":[{"label":"S","data":[1,2,3],"backgroundColor":["#7F22FE","#7F22FE","#7F22FE"],"borderColor":"#7F22FE","borderWidth":0,"borderSkipped":false,"fill":false,"maxBarThickness":62,"presentonBarRadius":{"horizontal":false,"radius":7},"pointBackgroundColor":["#7F22FE","#7F22FE","#7F22FE"],"pointBorderColor":"#FFFFFF","pointBorderWidth":0,"pointRadius":0,"tension":0}]},"options":{"color":"#475467","font":{"family":"Inter, Arial, sans-serif"},"indexAxis":"x","layout":{"padding":{"top":12,"right":22,"bottom":8,"left":12}},"responsive":false,"maintainAspectRatio":false,"animation":false,"normalized":true,"plugins":{"legend":{"display":true,"position":"bottom","labels":{"boxWidth":8,"boxHeight":8,"color":"#475467","font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"padding":9,"usePointStyle":true}},"title":{"display":false,"text":[],"color":"#344054","font":{"family":"Inter, Arial, sans-serif","size":11,"weight":"700"},"padding":{"bottom":16,"top":0}},"tooltip":{"enabled":false},"datalabels":{"display":false},"presentonDataLabels":{"enabled":false,"color":"#475467","fontFamily":"Inter, Arial, sans-serif","fontSize":8,"horizontal":false,"position":"top"}},"scales":{"x":{"display":true,"border":{"color":"#98A2B3","display":true},"grid":{"color":"rgba(152, 162, 179, 0.25)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":600},"maxRotation":0,"autoSkip":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"category"},"y":{"beginAtZero":true,"display":true,"border":{"color":"#98A2B3","display":true},"grace":"8%","grid":{"color":"rgba(152, 162, 179, 0.35)","display":true,"drawTicks":true},"stacked":false,"ticks":{"color":"#98A2B3","display":true,"font":{"family":"Inter, Arial, sans-serif","size":8,"weight":600},"presentonFormat":true},"title":{"color":"#98A2B3","display":false,"font":{"family":"Inter, Arial, sans-serif","size":9,"weight":700},"text":""},"type":"linear"}}}});
});
