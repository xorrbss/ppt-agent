import * as z from "zod";

export const imageSchema = z.object({
    url: z.url().meta({
        description: "URL to image",
    }),
    prompt: z.string().meta({
        description: "Prompt used to generate the image",
    }),
}).meta({
    imageType: 'image',
})

export const IconSchema = z.object({
    url: z.string().meta({
        description: "URL to icon",
    }),
    prompt: z.string().meta({
        description: "Prompt used to generate the icon",
    }),
}).meta({
    imageType: 'icon',
})