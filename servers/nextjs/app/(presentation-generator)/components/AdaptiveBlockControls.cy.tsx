// Cypress component test for the P4b adaptive block-CRUD affordance: verifies the
// click -> dispatch -> store round-trip (the link the node helper tests in
// lib/adaptiveBlockEdit.test.mjs can't cover).
//
// STATUS: not yet run here — the cypress binary install hangs in this environment
// (the same Cypress-CDN stall seen in the Docker build). This spec is
// correct-by-construction against the established mount pattern and runs in the
// `test-all` cypress step / once the binary is available.
import React from 'react'
import AdaptiveBlockControls from './AdaptiveBlockControls'
import { mount } from 'cypress/react'
import { store } from '@/store/store'
import { setPresentationData } from '@/store/slices/presentationGeneration'
import { Provider } from 'react-redux'
import '@/app/globals.css'

Cypress.Commands.add('mount', (component: React.ReactNode, options = {}) =>
  mount(<Provider store={store}>{component}</Provider>, options)
)

const makeBlocks = () => [
  { id: 'title', type: 'title', text: 'T' },
  { id: 'bullets', type: 'bullets', items: [
    { id: 'b1', text: 'B1' }, { id: 'b2', text: 'B2' }, { id: 'b3', text: 'B3' },
  ] },
  { id: 'card1', type: 'card', title: 'C1', text: 'X' },
  { id: 'card2', type: 'card', title: 'C2', text: 'Y' },
]

const seed = () => {
  const blocks = makeBlocks()
  store.dispatch(setPresentationData({
    slides: [{ id: 's0', index: 0, layout: 'adaptive', layout_group: 'adaptive',
      content: { archetype: 'one-column-bullets', blocks } }],
  } as any))
  return blocks
}

const slideBlocks = () =>
  (store.getState() as any).presentationGeneration.presentationData.slides[0].content.blocks
const bulletItems = () => slideBlocks().find((b: any) => b.id === 'bullets').items
const cards = () => slideBlocks().filter((b: any) => b.type === 'card')

describe('AdaptiveBlockControls (P4b)', () => {
  it('lists the slide\'s repeatable units', () => {
    const blocks = seed()
    cy.mount(<AdaptiveBlockControls slideIndex={0} blocks={blocks} />)
    cy.contains('블록 편집')
    cy.contains('불릿 1')
    cy.contains('카드')
  })

  it('delete removes only that unit from the store', () => {
    const blocks = seed()
    cy.mount(<AdaptiveBlockControls slideIndex={0} blocks={blocks} />)
    cy.get('[title="삭제"]').first().click()
    cy.then(() => {
      expect(bulletItems().map((i: any) => i.id)).to.deep.equal(['b2', 'b3'])
    })
  })

  it('add inserts a blank sibling after the unit', () => {
    const blocks = seed()
    cy.mount(<AdaptiveBlockControls slideIndex={0} blocks={blocks} />)
    cy.get('[title="아래에 추가"]').first().click()
    cy.then(() => {
      const items = bulletItems()
      expect(items).to.have.length(4)
      expect(items[1].text).to.equal('') // blank, inserted after b1
    })
  })

  it('move reorders within the array', () => {
    const blocks = seed()
    cy.mount(<AdaptiveBlockControls slideIndex={0} blocks={blocks} />)
    cy.get('[title="아래로"]').first().click() // move b1 down
    cy.then(() => {
      expect(bulletItems().map((i: any) => i.id)).to.deep.equal(['b2', 'b1', 'b3'])
    })
  })

  it('delete works on a top-level card block', () => {
    const blocks = seed()
    cy.mount(<AdaptiveBlockControls slideIndex={0} blocks={blocks} />)
    cy.contains('li', '카드').first().find('[title="삭제"]').click()
    cy.then(() => {
      expect(cards()).to.have.length(1)
    })
  })
})
