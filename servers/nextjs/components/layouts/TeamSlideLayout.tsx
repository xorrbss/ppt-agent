import React from 'react'
import * as z from "zod";
import { imageSchema } from './defaultSchemes';

export const layoutId = 'team-slide'
export const layoutName = 'Team Slide'
export const layoutDescription = 'A slide with a title, subtitle, and team members'

const teamSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Meet Our Team').meta({
        description: "Title of the slide",
    }),
    subtitle: z.string().min(3).max(150).optional().meta({
        description: "Optional subtitle or team description",
    }),
    teamMembers: z.array(z.object({
        name: z.string().min(2).max(100).meta({
            description: "Team member name",
        }),
        title: z.string().min(2).max(100).meta({
            description: "Job title or role",
        }),
        image: z.string().optional().meta({
            description: "URL to team member photo",
        }),
        bio: z.string().min(10).max(300).optional().meta({
            description: "Brief biography or description",
        }),
        email: z.string().email().optional().meta({
            description: "Contact email",
        }),
        linkedin: z.string().url().optional().meta({
            description: "LinkedIn profile URL",
        })
    })).min(1).max(6).default([
        {
            name: 'Sarah Johnson',
            title: 'Chief Executive Officer',
            image: 'https://images.unsplash.com/photo-1494790108755-2616b612b786?w=300&h=300&fit=crop&crop=face',
            bio: 'Strategic leader with 15+ years experience driving innovation and growth in technology companies.',
            email: 'sarah@company.com',
            linkedin: 'https://linkedin.com/in/sarahjohnson'
        },
        {
            name: 'Michael Chen',
            title: 'Chief Technology Officer',
            image: 'https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?w=300&h=300&fit=crop&crop=face',
            bio: 'Technology visionary specializing in scalable architecture and emerging technologies.',
            email: 'michael@company.com',
            linkedin: 'https://linkedin.com/in/michaelchen'
        },
        {
            name: 'Emma Rodriguez',
            title: 'Head of Design',
            image: 'https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=300&h=300&fit=crop&crop=face',
            bio: 'Creative director passionate about user-centered design and innovative digital experiences.',
            email: 'emma@company.com',
            linkedin: 'https://linkedin.com/in/emmarodriguez'
        }
    ]).describe('Team members (1-6 people)'),
    backgroundImage: imageSchema.optional().meta({
        description: "Background image for the slide",
    }),
})

export const Schema = teamSlideSchema

export type TeamSlideData = z.infer<typeof teamSlideSchema>

interface TeamSlideLayoutProps {
    data?: Partial<TeamSlideData>
    accentColor?: 'blue' | 'green' | 'purple' | 'orange' | 'red'
}

const TeamSlideLayout: React.FC<TeamSlideLayoutProps> = ({ data: slideData, accentColor = 'blue' }) => {

    const accentColors = {
        blue: 'from-blue-600 to-blue-800',
        green: 'from-emerald-600 to-emerald-800',
        purple: 'from-violet-600 to-violet-800',
        orange: 'from-orange-600 to-orange-800',
        red: 'from-red-600 to-red-800'
    }

    const accentSolids = {
        blue: 'bg-blue-600',
        green: 'bg-emerald-600',
        purple: 'bg-violet-600',
        orange: 'bg-orange-600',
        red: 'bg-red-600'
    }

    const iconColors = {
        blue: 'text-blue-600 hover:text-blue-700',
        green: 'text-emerald-600 hover:text-emerald-700',
        purple: 'text-violet-600 hover:text-violet-700',
        orange: 'text-orange-600 hover:text-orange-700',
        red: 'text-red-600 hover:text-red-700'
    }

    return (
        <div
            className="relative w-full aspect-[16/9] flex flex-col bg-gradient-to-br from-slate-50 via-white to-slate-100 overflow-hidden shadow-2xl border border-slate-200"
            style={slideData?.backgroundImage ? {
                backgroundImage: `linear-gradient(135deg, rgba(0,0,0,0.5), rgba(0,0,0,0.7)), url(${slideData?.backgroundImage})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center'
            } : {}}
        >
            {/* Enhanced geometric background decoration */}
            <div className="absolute inset-0 opacity-[0.03]">
                <div className={`absolute top-0 right-0 w-96 h-96 ${accentSolids[accentColor]} rounded-full transform translate-x-32 -translate-y-32 blur-3xl`} />
                <div className={`absolute bottom-0 left-0 w-64 h-64 ${accentSolids[accentColor]} rounded-full transform -translate-x-16 translate-y-16 blur-2xl`} />
            </div>

            <div className="relative z-10 flex flex-col h-full px-8 py-8">
                {/* Professional Header */}
                <header className="mb-6">
                    <h1 className={`text-4xl md:text-5xl font-bold mb-3 tracking-tight leading-tight break-words ${slideData?.backgroundImage
                        ? 'text-white drop-shadow-lg'
                        : 'text-slate-900'
                        }`}>
                        <span className={`bg-gradient-to-r ${accentColors[accentColor]} bg-clip-text text-transparent`}>
                            {slideData?.title}
                        </span>
                    </h1>

                    {slideData?.subtitle && (
                        <p className={`text-xl font-light leading-relaxed break-words ${slideData?.backgroundImage
                            ? 'text-slate-200 drop-shadow-md'
                            : 'text-slate-600'
                            }`}>
                            {slideData?.subtitle}
                        </p>
                    )}

                    <div className="relative mt-4">
                        <div className={`w-32 h-1 bg-gradient-to-r ${accentColors[accentColor]} rounded-full shadow-lg`} />
                        <div className={`absolute inset-0 w-32 h-1 bg-gradient-to-r ${accentColors[accentColor]} rounded-full blur-sm opacity-50`} />
                    </div>
                </header>

                {/* Enhanced Team Grid */}
                <main className="flex-1 flex items-center justify-center">
                    <div className={`grid gap-6 w-full max-w-6xl ${slideData?.teamMembers?.length && slideData?.teamMembers?.length <= 2 ? 'grid-cols-2' :
                        slideData?.teamMembers?.length && slideData?.teamMembers?.length <= 3 ? 'grid-cols-3' :
                            slideData?.teamMembers?.length && slideData?.teamMembers?.length <= 4 ? 'grid-cols-2 lg:grid-cols-4' :
                                'grid-cols-2 lg:grid-cols-3'
                        }`}>
                        {slideData?.teamMembers?.map((member, index) => (
                            <div key={index} className="bg-white/95 backdrop-blur-lg rounded-2xl p-6 shadow-2xl border border-white/50 text-center relative overflow-hidden group hover:transform hover:scale-105 transition-all duration-300">
                                {/* Card accent */}
                                <div className={`absolute top-0 left-0 right-0 h-1 bg-gradient-to-r ${accentColors[accentColor]}`} />

                                {/* Professional Avatar */}
                                <div className="relative mb-4 inline-block">
                                    <div className="w-20 h-20 mx-auto relative">
                                        {member.image ? (
                                            <img
                                                src={member.image}
                                                alt={member.name}
                                                className="w-full h-full object-cover rounded-full shadow-xl border-4 border-white group-hover:shadow-2xl transition-shadow duration-300"
                                            />
                                        ) : (
                                            <div className={`w-full h-full rounded-full ${accentSolids[accentColor]} flex items-center justify-center text-white font-bold text-xl shadow-xl border-4 border-white`}>
                                                {member.name.split(' ').map(n => n[0]).join('')}
                                            </div>
                                        )}
                                        <div className={`absolute inset-0 rounded-full bg-gradient-to-r ${accentColors[accentColor]} opacity-20 group-hover:opacity-30 transition-opacity duration-300`} />
                                    </div>
                                </div>

                                {/* Member Info */}
                                <div className="mb-4">
                                    <h3 className="text-xl font-bold text-slate-900 mb-1 break-words">
                                        {member.name}
                                    </h3>
                                    <p className={`text-sm font-semibold mb-3 bg-gradient-to-r ${accentColors[accentColor]} bg-clip-text text-transparent`}>
                                        {member.title}
                                    </p>

                                    {member.bio && (
                                        <p className="text-xs text-slate-600 leading-relaxed break-words font-medium">
                                            {member.bio}
                                        </p>
                                    )}
                                </div>

                                {/* Professional Contact Links */}
                                <div className="flex justify-center space-x-3">
                                    {member.email && (
                                        <a
                                            href={`mailto:${member.email}`}
                                            className={`p-2 rounded-full bg-slate-100 hover:bg-slate-200 ${iconColors[accentColor]} transition-all duration-200 hover:transform hover:scale-110`}
                                            title="Email"
                                        >
                                            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                                                <path d="M2.003 5.884L10 9.882l7.997-3.998A2 2 0 0016 4H4a2 2 0 00-1.997 1.884z" />
                                                <path d="M18 8.118l-8 4-8-4V14a2 2 0 002 2h12a2 2 0 002-2V8.118z" />
                                            </svg>
                                        </a>
                                    )}

                                    {member.linkedin && (
                                        <a
                                            href={member.linkedin}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className={`p-2 rounded-full bg-slate-100 hover:bg-slate-200 ${iconColors[accentColor]} transition-all duration-200 hover:transform hover:scale-110`}
                                            title="LinkedIn"
                                        >
                                            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                                                <path fillRule="evenodd" d="M16.338 16.338H13.67V12.16c0-.995-.017-2.277-1.387-2.277-1.39 0-1.601 1.086-1.601 2.207v4.248H8.014v-8.59h2.559v1.174h.037c.356-.675 1.227-1.387 2.526-1.387 2.703 0 3.203 1.778 3.203 4.092v4.711zM5.005 6.575a1.548 1.548 0 11-.003-3.096 1.548 1.548 0 01.003 3.096zm-1.337 9.763H6.34v-8.59H3.667v8.59zM17.668 1H2.328C1.595 1 1 1.581 1 2.298v15.403C1 18.418 1.595 19 2.328 19h15.34c.734 0 1.332-.582 1.332-1.299V2.298C19 1.581 18.402 1 17.668 1z" clipRule="evenodd" />
                                            </svg>
                                        </a>
                                    )}
                                </div>

                                {/* Background decoration */}
                                <div className={`absolute bottom-0 right-0 w-12 h-12 bg-gradient-to-tl ${accentColors[accentColor]} opacity-5 rounded-tl-full`} />
                            </div>
                        ))}
                    </div>
                </main>
            </div>

            {/* Enhanced decorative accent */}
            <div className={`absolute bottom-0 left-0 right-0 h-3 bg-gradient-to-r ${accentColors[accentColor]} shadow-lg`}>
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent" />
                <div className={`absolute inset-0 bg-gradient-to-r ${accentColors[accentColor]} blur-sm opacity-50`} />
            </div>

            {/* Professional corner accents */}
            <div className={`absolute top-0 right-0 w-32 h-32 bg-gradient-to-bl ${accentColors[accentColor]} opacity-5 rounded-bl-full`} />
        </div>
    )
}

export default TeamSlideLayout 