import React from 'react'
import { ChartLeaf } from './parts'
import { mount } from 'cypress/react'

// #4 multi-series charts. ChartLeaf uses Recharts (SVG). Its outer div is h-full
// w-full, but the cypress webpack config loads CSS as a string (Tailwind not
// applied), so inject just the 3 utility rules ChartLeaf needs + a fixed-size box
// → ResponsiveContainer gets real dimensions and renders.
const wrap = (block: any) =>
  mount(<div style={{ width: 480, height: 320 }}><ChartLeaf block={block} /></div>)

describe('ChartLeaf multi-series (#4)', () => {
  beforeEach(() => {
    cy.document().then((doc) => {
      if (doc.getElementById('cl-util')) return
      const style = doc.createElement('style')
      style.id = 'cl-util'
      style.textContent = '.h-full{height:100%}.w-full{width:100%}.min-h-0{min-height:0}'
      doc.head.appendChild(style)
    })
  })

  it('single-series bar renders one series and no legend (backward compat)', () => {
    wrap({ id: 'c', type: 'chart', chartType: 'bar', data: [{ name: 'a', value: 10 }, { name: 'b', value: 20 }] })
    cy.get('.recharts-bar', { timeout: 8000 }).should('have.length', 1)
    cy.get('.recharts-legend-wrapper').should('not.exist')
  })

  it('multi-series bar renders one series per name + a legend', () => {
    wrap({
      id: 'c', type: 'chart', chartType: 'bar', series: ['매출', '이익'],
      data: [{ name: '2023', value: 100, values: [100, 30] }, { name: '2024', value: 150, values: [150, 55] }],
    })
    cy.get('.recharts-bar', { timeout: 8000 }).should('have.length', 2)
    cy.get('.recharts-legend-wrapper').should('exist')
    cy.contains('매출')
    cy.contains('이익')
  })

  it('multi-series line renders one line per series', () => {
    wrap({
      id: 'c', type: 'chart', chartType: 'line', series: ['A', 'B'],
      data: [{ name: 'x', value: 1, values: [1, 2] }, { name: 'y', value: 3, values: [3, 4] }],
    })
    cy.get('.recharts-line', { timeout: 8000 }).should('have.length', 2)
  })
})
