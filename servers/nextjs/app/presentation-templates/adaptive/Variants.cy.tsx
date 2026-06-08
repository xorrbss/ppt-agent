import React from 'react'
import AdaptiveSlide from './AdaptiveSlide'
import { mount } from 'cypress/react'

// B-3 composition variants: assert the renderer dispatches on spec.variant, keeps
// the semantic-DOM/data-block-id export contract, and falls back to the default for
// missing/unknown variants. Tailwind isn't applied in the cypress webpack config, so
// assert on data-* attributes (not utility classes).

const cover = (variant?: string) => ({
  archetype: 'cover',
  ...(variant ? { variant } : {}),
  blocks: [
    { id: 'eyebrow', type: 'eyebrow', text: 'EB' },
    { id: 'title', type: 'title', text: 'Cover Title' },
    { id: 'subtitle', type: 'subtitle', text: 'Sub' },
  ],
})
const divider = (variant?: string) => ({
  archetype: 'section-divider',
  ...(variant ? { variant } : {}),
  blocks: [
    { id: 'eyebrow', type: 'eyebrow', text: 'Part 2' },
    { id: 'title', type: 'title', text: 'Roadmap' },
  ],
})
const statHero = (variant?: string) => ({
  archetype: 'stat-hero',
  ...(variant ? { variant } : {}),
  blocks: [
    { id: 'title', type: 'title', text: 'Outcomes' },
    { id: 's1', type: 'stat', value: '+12%', label: 'OEE' },
    { id: 's2', type: 'stat', value: '-30%', label: 'Downtime' },
  ],
})

describe('Adaptive composition variants (B-3)', () => {
  it('cover "left" dispatches + keeps editable leaves', () => {
    mount(<AdaptiveSlide data={cover('left') as any} />)
    cy.get('[data-variant="left"]').should('exist')
    cy.get('[data-block-id="title"]').should('exist')
    cy.get('[data-block-id="subtitle"]').should('exist')
  })

  it('section-divider "bold" dispatches + keeps eyebrow+title leaves', () => {
    mount(<AdaptiveSlide data={divider('bold') as any} />)
    cy.get('[data-variant="bold"]').should('exist')
    cy.get('[data-block-id="eyebrow"]').should('exist')
    cy.get('[data-block-id="title"]').should('exist')
  })

  it('stat-hero "featured" dispatches + keeps dotted stat ids', () => {
    mount(<AdaptiveSlide data={statHero('featured') as any} />)
    cy.get('[data-variant="featured"]').should('exist')
    cy.get('[data-block-id="s1.value"]').should('exist')
    cy.get('[data-block-id="s2.value"]').should('exist')
  })

  it('variant-less deck renders the default (no data-variant) — backward compat', () => {
    mount(<AdaptiveSlide data={statHero() as any} />)
    cy.get('[data-variant]').should('not.exist')
    cy.get('[data-block-id="s1.value"]').should('exist')
  })

  it('unknown variant falls back to the default composition without crashing', () => {
    mount(<AdaptiveSlide data={cover('bogus') as any} />)
    cy.get('[data-block-id="title"]').should('exist')
    cy.get('[data-block-id="subtitle"]').should('exist')
  })
})
