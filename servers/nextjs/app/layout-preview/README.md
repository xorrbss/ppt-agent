# Layout Preview Studio

A modular, responsive layout preview system for viewing and testing presentation layout components with realistic sample data.

## ✨ Features

- **Dynamic Layout Discovery**: Automatically discovers and loads layout components
- **Interactive Navigation**: Easy navigation with quick select grid
- **Beautiful Presentation Display**: Mock browser frame with professional styling
- **Detailed Information Panel**: Layout metadata and sample data inspection
- **Responsive Design**: Mobile-friendly with collapsible sidebar
- **Professional Loading States**: Skeleton loaders and error handling
- **Type Safety**: Full TypeScript support with shared types

## 🏗️ Architecture

The system is built with a modular architecture for maintainability and reusability:

```
layout-preview/
├── components/              # Modular UI components
│   ├── LayoutNavigation.tsx    # Navigation controls & layout selector
│   ├── LayoutDisplay.tsx       # Main layout preview area
│   ├── LayoutInfoPanel.tsx     # Information and data structure display
│   └── LoadingStates.tsx       # Loading, error, and empty states
├── hooks/                   # Custom React hooks
│   └── useLayoutLoader.ts      # Layout loading logic and state management
├── utils/                   # Utility functions
│   └── sampleDataGenerator.ts  # Realistic sample data generation
├── types/                   # Shared TypeScript types
│   └── index.ts                # Common interfaces and types
├── page.tsx                 # Main page component
└── README.md               # This file
```

## 🧩 Components

### LayoutNavigation
- Previous/Next navigation buttons
- Layout counter and current layout info
- Quick select grid for fast switching
- Responsive design with mobile optimization

### LayoutDisplay
- Mock browser frame presentation
- Layout rendering with sample data
- Professional shadow and styling effects
- Empty state with helpful messaging

### LayoutInfoPanel
- Layout metadata display
- Collapsible sample data viewer
- Copy to clipboard functionality
- Position tracking in collection

### LoadingStates
- Loading spinner with animated dots
- Error state with retry functionality
- Empty state with helpful instructions
- Skeleton loading animations

## 🎯 Custom Hooks

### useLayoutLoader
Handles all layout loading logic:
- Fetches layout files from API
- Imports and validates components
- Generates realistic sample data
- Provides retry functionality
- Manages loading and error states

## 🛠️ Utilities

### sampleDataGenerator
Intelligent sample data generation:
- Context-aware field value generation
- Support for images, emails, phones, URLs
- Realistic business content
- Zod schema parsing and validation
- Array and object handling

## 📱 Responsive Design

The layout preview system is fully responsive:
- **Desktop**: Sidebar navigation with main preview area
- **Tablet**: Collapsible navigation panels
- **Mobile**: Stacked layout with touch-friendly controls

## 🎨 Styling

Built with:
- **Tailwind CSS**: Utility-first styling
- **shadcn/ui**: Consistent component library
- **Gradient Backgrounds**: Modern visual appeal
- **Glass Morphism**: Backdrop blur effects
- **Smooth Animations**: Hover and transition effects

## 🔧 Usage

The system automatically discovers layout components that export:
```typescript
// Layout component
export default function MyLayout({ data }: { data: any }) {
  return <div>{/* Your layout */}</div>
}

// Zod schema for type safety and sample data generation
export const Schema = z.object({
  title: z.string(),
  description: z.string(),
  // ... other fields
})
```

## 🚀 Getting Started

1. **Add Layout Components**: Place your layout files in the appropriate directory
2. **Export Requirements**: Ensure each layout exports both a default component and Schema
3. **Navigate**: Use the navigation controls or quick select grid
4. **Inspect**: View layout information and sample data structure
5. **Test**: See how your layouts render with realistic data

## 🎯 Benefits

- **Modular Architecture**: Easy to maintain and extend
- **Type Safety**: Full TypeScript support prevents runtime errors
- **Beautiful UI**: Professional design that's pleasant to use
- **Developer Experience**: Quick feedback loop for layout development
- **Responsive**: Works on all device sizes
- **Accessible**: Keyboard navigation and screen reader support

## 📈 Performance

- **Lazy Loading**: Components are imported only when needed
- **Optimized Rendering**: Efficient re-renders with React best practices
- **Minimal Bundle**: Modular structure enables tree shaking
- **Caching**: Sample data generation is memoized 