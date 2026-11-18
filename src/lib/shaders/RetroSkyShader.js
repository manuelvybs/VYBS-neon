import * as THREE from 'three';

// Synthwave sunset sky: purple-to-orange gradient, a sun with horizontal gaps in its lower half, and a few stars.
// Colours are written as display (sRGB) values; ShaderMaterial output is not re-encoded.
export let RetroSkyShader = {
	uniforms: {
		sunPosition: { value: new THREE.Vector3() }
	},

	vertexShader: `
		varying vec3 vDir;
		void main() {
			vDir = position;
			gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
		}
	`,

	fragmentShader: `
		uniform vec3 sunPosition;
		varying vec3 vDir;

		float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

		void main() {
			vec3 d = normalize(vDir);
			float h = d.y;

			vec3 top  = vec3(0.106, 0.043, 0.200);   // #1B0B33
			vec3 mid  = vec3(0.357, 0.102, 0.478);   // #5B1A7A
			vec3 pink = vec3(1.000, 0.180, 0.592);   // #FF2E97
			vec3 orng = vec3(1.000, 0.482, 0.227);   // #FF7B3A

			vec3 col;
			if (h > 0.30)      col = mix(mid, top, smoothstep(0.30, 0.75, h));
			else if (h > 0.07) col = mix(pink, mid, smoothstep(0.07, 0.30, h));
			else if (h > 0.0)  col = mix(orng, pink, smoothstep(0.0, 0.07, h));
			else               col = mix(orng * 0.55, top, smoothstep(0.0, -0.12, h));

			// stars in the upper sky
			if (h > 0.22) {
				vec2 cell = floor(vec2(atan(d.z, d.x) * 160.0, h * 160.0));
				float s = hash(cell);
				if (s > 0.9965) col += vec3(0.9, 0.85, 1.0) * smoothstep(0.22, 0.45, h);
			}

			// banded sun
			vec3 sd = normalize(sunPosition);
			float ang = acos(clamp(dot(d, sd), -1.0, 1.0));
			float R = 0.17;
			col += pink * 0.35 * smoothstep(0.75, 0.0, ang) * smoothstep(-0.04, 0.12, h);
			if (ang < R) {
				float t = clamp((d.y - (sd.y - R)) / (2.0 * R), 0.0, 1.0);
				vec3 sunCol = mix(pink, vec3(1.0, 0.894, 0.361), smoothstep(0.1, 0.9, t));
				float band = 1.0;
				if (t < 0.55) {
					float k = fract((0.55 - t) * 8.0);
					band = step(0.12 + (0.55 - t) * 1.1, k);
				}
				col = mix(col, sunCol, band * smoothstep(R, R - 0.008, ang));
			}

			gl_FragColor = vec4(col, 1.0);
		}
	`
};
