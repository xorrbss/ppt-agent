import * as z from "zod";
// Note:
// If you want to use Image and Icon Must Use these Schemas.
// Image and icons are only media support for PDF and PPTX.
import { ImageSchema, IconSchema } from "./defaultSchemes";


// Schema definition
export const Schema = z.object({
    // Note:
    // Schema fields
    // Each fields must have a default value (this is important for Layout-preview)
    // Each fields must have a meta description
    // Each fields must have a min and max length, length must support the layout design.
    // Each Array fields must have a min and max length

})

// Type inference
type SchemaType = z.infer<typeof Schema>;


// Component definition
const SlideStructure = ({ data }: { data: Partial<SchemaType> }) => {
    // Note: 
    // Data is already parse so not need to parse again.
    // Must have consistent aspect ratio (16:9) and max-width 1280px.
    // Validate each data before rendering using && operator or optional chaining
    // These layout are exported as PDF and PPTX so must be optimized for both.
    // Component design, layout, styles, etc.
    // Content must be properly fit in the container, if need adjust the lenght of the content and items in Schema.
    // See ExampleComponent.tsx for more details.
    // Font Size must fit the desing and and the content must be properly fit in the container.
};

export default SlideStructure;