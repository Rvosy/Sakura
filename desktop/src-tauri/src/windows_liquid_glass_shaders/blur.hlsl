// Ported from Liquid Glass Studio (https://github.com/iyinchao/liquid-glass-studio).
// Copyright 2024 Charles Yin. MIT License; see the upstream repository.

Texture2D<float4> sourceTexture : register(t0);
SamplerState linearClamp : register(s0);
cbuffer BlurParameters : register(b0) {
    float2 texelSize; float2 direction;
    float2 sourceOffset; float2 sourceScale;
    float sigma; float3 _padding;
};

float gaussian(float x, float standardDeviation) {
    return exp(-0.5 * x * x / max(standardDeviation * standardDeviation, 0.0001));
}

float4 ps_blur(float4 position : SV_Position, float2 uv : TEXCOORD0) : SV_Target {
    int radius = min(64, max(1, (int)ceil(sigma * 3.0)));
    float2 sourceUv = sourceOffset + uv * sourceScale;
    float4 color = sourceTexture.Sample(linearClamp, sourceUv);
    float weightSum = 1.0;
    [loop] for (int i = 1; i <= radius; ++i) {
        float weight = gaussian((float)i, sigma);
        float2 offset = direction * texelSize * (float)i;
        color += sourceTexture.Sample(linearClamp, sourceUv + offset) * weight;
        color += sourceTexture.Sample(linearClamp, sourceUv - offset) * weight;
        weightSum += weight * 2.0;
    }
    return color / weightSum;
}
