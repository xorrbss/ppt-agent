import * as z from "zod";

export const ImageSchema = z.object({
    url: z.url().meta({
        description: "URL to image",
    }),
    prompt: z.string().meta({
        description: "Prompt used to generate the image",
    }),
    __image_type__:z.literal('image')
})

export const IconSchema = z.object({
    url: z.string().meta({
        description: "URL to icon",
    }),
    prompt: z.string().meta({
        description: "Prompt used to generate the icon",
    }),
    __image_type__:z.literal('icon')
})