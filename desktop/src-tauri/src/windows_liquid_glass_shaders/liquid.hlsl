// HLSL port of Liquid Glass Studio's SDF/refraction/composite shader.
// https://github.com/iyinchao/liquid-glass-studio
// Copyright 2024 Charles Yin. MIT License; see the upstream repository.

#define PI 3.14159265359
Texture2D<float4> backgroundTexture : register(t0);
Texture2D<float4> blurredTexture : register(t1);
SamplerState linearClamp : register(s0);

cbuffer LiquidParameters : register(b0) {
    float2 resolution; float2 shapeCenter;
    float2 shapeSize; float cornerRadius; float refractionThickness;
    float refractionFactor; float dispersion; float2 _opticsPadding;
    float fresnelRange; float fresnelHardness; float fresnelFactor; float glareRange;
    float glareAngle; float glareFactor; float glareOppositeFactor; float glareConvergence;
    float glareHardness; float3 _glarePadding;
    uint debugStep; float3 _modePadding;
    float3 tint; float tintAlpha;
    float2 backgroundOffset; float2 backgroundScale;
    float2 blurredOffset; float2 blurredScale;
};

float4 sampleBackground(float2 uv) {
    return backgroundTexture.Sample(linearClamp, backgroundOffset + uv * backgroundScale);
}

float4 sampleBlurred(float2 uv) {
    return blurredTexture.Sample(linearClamp, blurredOffset + uv * blurredScale);
}

float roundedRectSdf(float2 samplePosition, float2 halfSize, float radius) {
    float2 q = abs(samplePosition) - halfSize + radius;
    return min(max(q.x, q.y), 0.0) + length(max(q, 0.0)) - radius;
}

float edgeFactor(float depth) {
    if (depth < 0.0 || depth >= refractionThickness) return 0.0;
    float ratio = saturate(1.0 - depth / max(refractionThickness, 0.0001));
    float thetaI = asin(clamp(ratio * ratio, -1.0, 1.0));
    float thetaT = asin(clamp(sin(thetaI) / max(refractionFactor, 0.0001), -1.0, 1.0));
    return max(0.0, -tan(thetaT - thetaI));
}

float2 sdfNormal(float2 samplePosition, float2 halfSize, float radius) {
    float epsilon = 0.5;
    float dx = roundedRectSdf(samplePosition + float2(epsilon, 0.0), halfSize, radius)
             - roundedRectSdf(samplePosition - float2(epsilon, 0.0), halfSize, radius);
    float dy = roundedRectSdf(samplePosition + float2(0.0, epsilon), halfSize, radius)
             - roundedRectSdf(samplePosition - float2(0.0, epsilon), halfSize, radius);
    float2 gradient = float2(dx, dy);
    float lengthSquared = dot(gradient, gradient);
    return lengthSquared > 0.000001 ? gradient * rsqrt(lengthSquared) : float2(0.0, -1.0);
}

float4 dispersionSample(float2 uv, float2 offset, float blurMix) {
    const float3 indices = float3(0.98, 1.0, 1.02);
    float3 result;
    float2 uvR = uv + offset * (1.0 - (indices.r - 1.0) * dispersion);
    float2 uvG = uv + offset * (1.0 - (indices.g - 1.0) * dispersion);
    float2 uvB = uv + offset * (1.0 - (indices.b - 1.0) * dispersion);
    result.r = lerp(sampleBackground(uvR).r, sampleBlurred(uvR).r, blurMix);
    result.g = lerp(sampleBackground(uvG).g, sampleBlurred(uvG).g, blurMix);
    result.b = lerp(sampleBackground(uvB).b, sampleBlurred(uvB).b, blurMix);
    return float4(result, 1.0);
}

float4 ps_liquid(float4 position : SV_Position, float2 uv : TEXCOORD0) : SV_Target {
    float2 samplePosition = position.xy - shapeCenter;
    float sdf = roundedRectSdf(samplePosition, shapeSize * 0.5, cornerRadius);
    float4 background = sampleBackground(uv);
    if (sdf > 0.5) return background;

    float depth = max(0.0, -sdf);
    float edge = edgeFactor(depth);
    float2 normal = sdfNormal(samplePosition, shapeSize * 0.5, cornerRadius);
    if (debugStep == 0) return float4(1.0, 0.0, 168.0 / 255.0, 1.0);
    if (debugStep == 1) return float4(frac(abs(sdf) * 0.15).xxx, 1.0);
    if (debugStep == 2) return float4(normal * 0.5 + 0.5, 0.5, 1.0);
    if (debugStep == 3) return float4(edge.xxx, 1.0);
    if (debugStep == 4) return float4(abs(normal) * edge, 0.0, 1.0);
    if (debugStep == 5) return sampleBlurred(uv);

    float2 offset = -normal * edge * 0.05 * float2(resolution.y / max(resolution.x, 1.0), 1.0);
    float blurMix = edge > 0.0 ? saturate(depth / max(refractionThickness, 0.0001)) : 1.0;
    float4 color = edge > 0.0 ? dispersionSample(uv, offset, blurMix) : sampleBlurred(uv);
    if (debugStep == 6) return color;

    float fresnel = saturate(pow(1.0 + sdf / 1500.0 * pow(500.0 / max(fresnelRange, 0.0001), 2.0) + fresnelHardness, 5.0));
    color.rgb = lerp(color.rgb, 1.0.xxx, fresnel * fresnelFactor * 0.7);
    if (debugStep == 7) return color;

    float angle = (atan2(normal.y, normal.x) - PI / 4.0 + glareAngle) * 2.0;
    float farSide = (angle > PI * 1.5 && angle < PI * 3.5) || angle < -PI * 0.5 ? glareOppositeFactor : 1.0;
    float angular = saturate(pow(saturate((0.5 + sin(angle) * 0.5) * 1.2 * farSide * glareFactor), 0.1 + glareConvergence * 2.0));
    float glareGeometry = saturate(pow(1.0 + sdf / 1500.0 * pow(500.0 / max(glareRange, 0.0001), 2.0) + glareHardness, 5.0));
    color.rgb = lerp(color.rgb, 1.0.xxx, angular * glareGeometry);
    if (debugStep == 8) return color;

    color.rgb = lerp(color.rgb, tint, tintAlpha * 0.8);
    float edgeBlend = smoothstep(-1.0, 1.0, sdf);
    return lerp(color, background, edgeBlend);
}
