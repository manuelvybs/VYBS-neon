import { RetroSkyShader } from '../../lib/shaders/RetroSkyShader';
import * as THREE from 'three';
import { World } from './World';
import { EntityType } from '../enums/EntityType';
import { IUpdatable } from '../interfaces/IUpdatable';
import { default as CSM } from 'three-csm';

export class Sky extends THREE.Object3D implements IUpdatable
{
	public updateOrder: number = 5;

	public sunPosition: THREE.Vector3 = new THREE.Vector3();
	public csm: CSM;

	set theta(value: number) {
		this._theta = value;
		this.refreshSunPosition();
	}

	set phi(value: number) {
		this._phi = value;
		this.refreshSunPosition();
		this.refreshHemiIntensity();
	}

	private _phi: number = 22;
	private _theta: number = 145;

	private hemiLight: THREE.HemisphereLight;
	private maxHemiIntensity: number = 0.85;
	private minHemiIntensity: number = 0.85;

	private skyMesh: THREE.Mesh;
	private skyMaterial: THREE.ShaderMaterial;

	private world: World;

	constructor(world: World)
	{
		super();

		this.world = world;
		
		// Sky material
		this.skyMaterial = new THREE.ShaderMaterial({
			uniforms: THREE.UniformsUtils.clone(RetroSkyShader.uniforms),
			fragmentShader: RetroSkyShader.fragmentShader,
			vertexShader: RetroSkyShader.vertexShader,
			side: THREE.BackSide,
			depthWrite: false
		});

		// Mesh
		this.skyMesh = new THREE.Mesh(
			new THREE.SphereBufferGeometry(1000, 24, 12),
			this.skyMaterial
		);
		this.attach(this.skyMesh);

		// Ambient light
		this.hemiLight = new THREE.HemisphereLight( 0xffffff, 0xffffff, 1.0 );
		this.refreshHemiIntensity();
		// Night: lavender from above, deep purple bounce from below
		this.hemiLight.color.setHex(0xC9B6FF).convertSRGBToLinear();
		this.hemiLight.groundColor.setHex(0x4A2C7A).convertSRGBToLinear();
		this.hemiLight.position.set( 0, 50, 0 );
		this.world.graphicsWorld.add( this.hemiLight );

		// CSM
		// New version
		// let splitsCallback = (amount, near, far, target) =>
		// {
		// 	for (let i = amount - 1; i >= 0; i--)
		// 	{
		// 		target.push(Math.pow(1 / 3, i));
		// 	}
		// };

		// Legacy
		let splitsCallback = (amount, near, far) =>
		{
			let arr = [];

			for (let i = amount - 1; i >= 0; i--)
			{
				arr.push(Math.pow(1 / 4, i));
			}

			return arr;
		};

		this.csm = new CSM({
			fov: 80,
			far: 250,	// maxFar
			lightIntensity: 1.1,
			cascades: 3,
			shadowMapSize: 2048,
			camera: world.camera,
			parent: world.graphicsWorld,
			mode: 'custom',
			customSplitsCallback: splitsCallback
		});
		this.csm.fade = true;
		// Warm pink sunset key light
		this.csm.lights.forEach((light) => light.color.setHex(0xFFB8DC).convertSRGBToLinear());

		this.refreshSunPosition();
		
		world.graphicsWorld.add(this);
		world.registerUpdatable(this);
	}

	public update(timeScale: number): void
	{
		this.position.copy(this.world.camera.position);
		this.refreshSunPosition();

		this.csm.update(this.world.camera.matrix);
		this.csm.lightDirection = new THREE.Vector3(-this.sunPosition.x, -this.sunPosition.y, -this.sunPosition.z).normalize();
	}

	public refreshSunPosition(): void
	{
		const sunDistance = 10;

		this.sunPosition.x = sunDistance * Math.sin(this._theta * Math.PI / 180) * Math.cos(this._phi * Math.PI / 180);
		this.sunPosition.y = sunDistance * Math.sin(this._phi * Math.PI / 180);
		this.sunPosition.z = sunDistance * Math.cos(this._theta * Math.PI / 180) * Math.cos(this._phi * Math.PI / 180);

		this.skyMaterial.uniforms.sunPosition.value.copy(this.sunPosition);
	}

	public refreshHemiIntensity(): void
	{
		this.hemiLight.intensity = this.minHemiIntensity + Math.pow(1 - (Math.abs(this._phi - 90) / 90), 0.25) * (this.maxHemiIntensity - this.minHemiIntensity);
	}
}