import React from 'react'
import ThemeComposer from './ThemeComposer'
import { mount } from 'cypress/react'

// A generated brand palette (theme_generate returns colours only).
const COLORS = {
  primary: '#1f6feb', background: '#0d1117', card: '#161b22', stroke: '#30363d',
  primary_text: '#ffffff', background_text: '#e6edf3',
  graph_0: '#79c0ff', graph_1: '#58a6ff', graph_2: '#4493f8', graph_3: '#3081f0',
  graph_4: '#1f6feb', graph_5: '#1158c7', graph_6: '#0d419d', graph_7: '#0a3069',
  graph_8: '#082145', graph_9: '#051026',
}

describe('<ThemeComposer />', () => {
  beforeEach(() => {
    cy.viewport(1100, 900)
    cy.intercept('POST', '**/ppt/theme/generate', { statusCode: 200, body: COLORS }).as('gen')
    cy.intercept('POST', '**/ppt/themes/create', { statusCode: 200, body: { id: 't1' } }).as('create')
    mount(<ThemeComposer />)
  })

  it('composes a styled theme from a style preset + brand colours and saves it', () => {
    cy.get('[data-testid="theme-composer"]').should('exist')
    // pick the "carbon" style (sharp + flat) so we can assert its tokens carry through
    cy.get('[data-testid="style-option-carbon"]').should('exist').click()
    cy.get('[data-testid="composer-generate"]').click()
    cy.wait('@gen')
    cy.get('[data-testid="composer-preview"]').should('exist')
    cy.get('[data-testid="composer-palette"]').should('exist')

    cy.get('[data-testid="composer-save"]').click()
    cy.wait('@create').its('request.body').then((body: any) => {
      expect(body.data.colors.primary).to.eq('#1f6feb')        // brand colours used
      expect(body.data.shape.radiusScale).to.eq(0)             // carbon style carried
      expect(body.data.elevation.flat).to.eq(true)             // carbon style carried
      expect(body.data.fonts).to.exist                          // style fonts carried
    })
  })
})
