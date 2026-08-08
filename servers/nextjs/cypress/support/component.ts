// ***********************************************************
// This example support/component.ts is processed and
// loaded automatically before your test files.
//
// This is a great place to put global configuration and
// behavior that modifies Cypress.
//
// You can change the location of this file or turn off
// automatically serving support files with the
// 'supportFile' configuration option.
//
// You can read more here:
// https://on.cypress.io/configuration
// ***********************************************************

// Import commands.js using ES2015 syntax:
import './commands'

// Components under test are styled entirely by Tailwind utilities, so without
// the app stylesheet they mount with no geometry at all. Specs then fail on
// overlaps and hit targets that no user ever meets.
import '../../app/globals.css'
import 'katex/dist/katex.min.css'

import { mount } from 'cypress/react'
import { ApiMockManager, waitForChartsToRender } from './testHelpers'

// Augment the Cypress namespace to include type definitions for
// your custom command.
// Alternatively, can be defined in cypress/support/component.d.ts
// with a <reference path="./component" /> at the top of your spec.
declare global {
  namespace Cypress {
    interface Chainable {
      mount: typeof mount
      waitForChartsToRender: typeof waitForChartsToRender
    }
  }
}

Cypress.Commands.add('mount', mount)
Cypress.Commands.add('waitForChartsToRender', waitForChartsToRender)

// Global test isolation: Clean up stubs and spies after each test
afterEach(() => {
  // Restore all stubbed and spied methods
  cy.window().then((win) => {
    if (win.sinon) {
      win.sinon.restore();
    }
  });
});

// Example use:
// cy.mount(<MyComponent />)
