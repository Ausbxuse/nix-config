// Ambient background for Ghostty: a slow aurora haze near the top and a few
// drifting, twinkling motes. Only pixels showing the terminal background are
// touched, so text and selections stay exactly as rendered.
//
// Cost is constant per pixel (no loops): the motes live on a grid and each
// pixel only looks at its own cell. Integer hashing and exp2 keep the work
// light on integrated GPUs.

float hash(vec2 p) {
    uvec2 q = uvec2(ivec2(p)) * uvec2(1597334673u, 3812015801u);
    uint n = (q.x ^ q.y) * 1597334673u;
    return float(n) * (1.0 / 4294967295.0);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 uv = fragCoord / iResolution.xy;
    vec4 term = texture(iChannel0, uv);

    // Ghostty's window padding always shows the background colour, so the
    // corner pixel tells us which pixels are background, in either theme.
    vec3 bg = texture(iChannel0, vec2(1.5) / iResolution.xy).rgb;
    vec3 diff = term.rgb - bg;
    float isBg = 1.0 - smoothstep(0.012, 0.05, sqrt(dot(diff, diff)));
    if (isBg <= 0.0) {
        fragColor = term;
        return;
    }

    float dark = step(dot(bg, vec3(0.2126, 0.7152, 0.0722)), 0.5);
    vec3 tint = mix(vec3(0.21, 0.44, 0.23), vec3(0.71, 0.75, 1.0), dark);
    float t = iTime;

    // Aurora: one soft band whose height and brightness drift slowly.
    float glow = 0.0;
    float y = 1.0 - uv.y;
    if (y < 0.35) {
        float centre = 0.12 + 0.05 * sin(uv.x * 2.3 + t * 0.05);
        float dy = y - centre;
        float shimmer = 0.6 + 0.4 * sin(uv.x * 7.0 - t * 0.11);
        glow = 0.04 * exp2(-240.0 * dy * dy) * shimmer;
    }

    // Motes: about a quarter of the grid cells hold one, rising slowly.
    const float cell = 180.0;
    vec2 g = fragCoord / cell + vec2(t * 0.004, -t * 0.012);
    vec2 id = floor(g);
    float h = hash(id);
    if (h > 0.75) {
        vec2 drift = 0.06 * sin(vec2(t * 0.21, t * 0.17 + 1.57) + h * vec2(20.0, 13.0));
        vec2 pos = 0.25 + 0.5 * vec2(hash(id + 17.0), hash(id + 71.0)) + drift;
        vec2 d = (fract(g) - pos) * cell;
        float twinkle = 0.5 + 0.5 * sin(t * (0.5 + h) + h * 40.0);
        glow += 0.18 * exp2(-0.045 * dot(d, d)) * twinkle;
    }

    vec3 lit = mix(mix(term.rgb, tint, glow), term.rgb + tint * glow, dark);
    fragColor = vec4(mix(term.rgb, lit, isBg), term.a);
}
