// Cypress component test for the #2 schema-driven property panel: the chart-type
// selector dispatches updateAdaptiveBlock and mutates slide.content.blocks.
import React from 'react'
import AdaptivePropertyControls from './AdaptivePropertyControls'
import { store } from '@/store/store'
import { setPresentationData } from '@/store/slices/presentationGeneration'
import { Provider } from 'react-redux'

const mountPanel = (blocks: any[]) =>
  cy.mount(
    <Provider store={store}>
      <AdaptivePropertyControls slideIndex={0} blocks={blocks} />
    </Provider>
  )

const seed = () => {
  const blocks = [
    { id: 'title', type: 'title', text: '차트 인사이트' },
    { id: 'chart1', type: 'chart', chartType: 'bar', data: [{ name: 'a', value: 1 }] },
    { id: 'insights', type: 'bullets', items: [{ id: 'b1', text: '요점' }] },
  ]
  store.dispatch(setPresentationData({
    slides: [{ id: 's0', index: 0, layout: 'adaptive', layout_group: 'adaptive',
      content: { archetype: 'chart-insight', blocks } }],
  } as any))
  return blocks
}

const chartType = () =>
  (store.getState() as any).presentationGeneration.presentationData.slides[0].content.blocks
    .find((b: any) => b.id === 'chart1').chartType

describe('AdaptivePropertyControls (#2)', () => {
  it('renders the chart-type selector only when a chart block exists', () => {
    const blocks = seed()
    mountPanel(blocks)
    cy.contains('속성 편집')
    cy.get('[data-testid="chart-type-chart1"]').should('have.value', 'bar')
  })

  it('does not render when there is no chart block', () => {
    store.dispatch(setPresentationData({
      slides: [{ id: 's0', index: 0, layout: 'adaptive', layout_group: 'adaptive',
        content: { archetype: 'one-column-bullets', blocks: [{ id: 't', type: 'title', text: 'x' }] } }],
    } as any))
    cy.mount(
      <Provider store={store}>
        <AdaptivePropertyControls slideIndex={0} blocks={[{ id: 't', type: 'title', text: 'x' }]} />
      </Provider>
    )
    cy.get('[data-adaptive-property-controls]').should('not.exist')
  })

  it('changing the chart type updates the block in the store', () => {
    const blocks = seed()
    mountPanel(blocks)
    cy.get('[data-testid="chart-type-chart1"]').select('pie')
    cy.then(() => {
      expect(chartType()).to.eq('pie')
    })
  })
})
