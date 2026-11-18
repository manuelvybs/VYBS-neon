import * as THREE from 'three';
import * as Utils from './FunctionLibrary';
import { World } from '../world/World';
import { IInputReceiver } from '../interfaces/IInputReceiver';
import { KeyBinding } from './KeyBinding';
import { Character } from '../characters/Character';
import _ = require('lodash');
import { IUpdatable } from '../interfaces/IUpdatable';

export class CameraOperator implements IInputReceiver, IUpdatable
{
	public updateOrder: number = 4;

	public world: World;
	public camera: THREE.Camera;
	public target: THREE.Vector3;
	public sensitivity: THREE.Vector2;
	public radius: number = 1;
	public theta: number;
	public phi: number;
	public onMouseDownPosition: THREE.Vector2;
	public onMouseDownTheta: any;
	public onMouseDownPhi: any;
	public targetRadius: number = 1;

	public movementSpeed: number;
	public actions: { [action: string]: KeyBinding };

	public upVelocity: number = 0;
	public forwardVelocity: number = 0;
	public rightVelocity: number = 0;

	public followMode: boolean = false;

	public characterCaller: Character;

	constructor(world: World, camera: THREE.Camera, sensitivityX: number = 1, sensitivityY: number = sensitivityX * 0.8)
	{
		this.world = world;
		this.camera = camera;
		this.target = new THREE.Vector3();
		this.sensitivity = new THREE.Vector2(sensitivityX, sensitivityY);

		this.movementSpeed = 0.06;
		this.radius = 3;
		this.theta = 0;
		this.phi = 0;

		this.onMouseDownPosition = new THREE.Vector2();
		this.onMouseDownTheta = this.theta;
		this.onMouseDownPhi = this.phi;

		this.actions = {
			'forward': new KeyBinding('KeyW'),
			'back': new KeyBinding('KeyS'),
			'left': new KeyBinding('KeyA'),
			'right': new KeyBinding('KeyD'),
			'up': new KeyBinding('KeyE'),
			'down': new KeyBinding('KeyQ'),
			'fast': new KeyBinding('ShiftLeft'),
		};

		world.registerUpdatable(this);
	}

	public setSensitivity(sensitivityX: number, sensitivityY: number = sensitivityX): void
	{
		this.sensitivity = new THREE.Vector2(sensitivityX, sensitivityY);
	}

	public setRadius(value: number, instantly: boolean = false): void
	{
		this.targetRadius = Math.max(0.001, value);
		if (instantly === true)
		{
			this.radius = value;
		}
	}

	// Classic top-down view: fixed yaw, steep pitch, pulled back. The mouse doesn't orbit it.
	public topDown: boolean = false;
	public topDownTheta: number = 0;
	public topDownPhi: number = 62;
	public topDownRadius: number = 6.5;
	private topDownBlend: number = 0;
	// Last time the mouse turned the camera; the chase cam waits a moment before taking over again
	private lastManualMove: number = -1e9;
	private chaseFwd: THREE.Vector3 = new THREE.Vector3();

	public setTopDown(enabled: boolean): void
	{
		this.topDown = enabled;
		if (this.world.params !== undefined) this.world.params.Top_Down = enabled;
		// Coming back to third person: start behind the target at a comfortable angle.
		if (!enabled)
		{
			this.theta = this.topDownTheta;
			this.phi = 15;
		}
	}

	public toggleTopDown(): void
	{
		this.setTopDown(!this.topDown);
	}

	// GTA-style driving camera: swing in behind whatever you are driving while it moves forward.
	// Mouse look still works; the camera waits 1.5 s after you last moved it before taking over again.
	private chaseCam(): void
	{
		if (this.isTopDownActive() || this.characterCaller !== undefined) return;
		if (performance.now() - this.lastManualMove < 1500) return;

		const receiver: any = this.world.inputManager.inputReceiver;
		if (receiver === undefined) return;
		const vehicle: any = receiver.controlledObject !== undefined ? receiver.controlledObject : (receiver.seats !== undefined ? receiver : undefined);
		if (vehicle === undefined || vehicle.collision === undefined || vehicle.chaseCamera !== true) return;
		if (typeof vehicle.isFirstPerson === 'function' && vehicle.isFirstPerson()) return;

		const vel = vehicle.collision.velocity;
		const speed = Math.sqrt(vel.x * vel.x + vel.z * vel.z);
		if (speed < 2) return;
		const fwd = this.chaseFwd.set(0, 0, 1).applyQuaternion(vehicle.quaternion);
		if (fwd.x * vel.x + fwd.z * vel.z < 0) return;  // reversing: leave the camera alone

		const desired = Math.atan2(-fwd.x, -fwd.z) * 180 / Math.PI;
		const d = (((desired - this.theta) % 360) + 540) % 360 - 180;
		const k = Math.min(0.12, 0.02 + speed * 0.004);
		this.theta += d * k;
		this.phi += (12 - this.phi) * 0.05;
	}

	// Top-down only applies while following a character or vehicle, not in free camera.
	private isTopDownActive(): boolean
	{
		return this.topDown && this.characterCaller === undefined;
	}

	public move(deltaX: number, deltaY: number): void
	{
		if (this.isTopDownActive()) return;
		if (deltaX !== 0 || deltaY !== 0) this.lastManualMove = performance.now();

		this.theta -= deltaX * (this.sensitivity.x / 2);
		this.theta %= 360;
		this.phi += deltaY * (this.sensitivity.y / 2);
		this.phi = Math.min(85, Math.max(-85, this.phi));
	}

	public update(timeScale: number): void
	{
		if (this.followMode === true)
		{
			this.camera.position.y = THREE.MathUtils.clamp(this.camera.position.y, this.target.y, Number.POSITIVE_INFINITY);
			this.camera.lookAt(this.target);
			let newPos = this.target.clone().add(new THREE.Vector3().subVectors(this.camera.position, this.target).normalize().multiplyScalar(this.targetRadius));
			this.camera.position.x = newPos.x;
			this.camera.position.y = newPos.y;
			this.camera.position.z = newPos.z;
		}
		else 
		{
			this.radius = THREE.MathUtils.lerp(this.radius, this.targetRadius, 0.1);
			this.chaseCam();

			// Ease between the orbit camera and the top-down camera instead of snapping.
			this.topDownBlend = THREE.MathUtils.lerp(this.topDownBlend, this.isTopDownActive() ? 1 : 0, 0.08);
			const b = this.topDownBlend;
			const dTheta = (((this.topDownTheta - this.theta) % 360) + 540) % 360 - 180;
			const theta = this.theta + dTheta * b;
			const phi = THREE.MathUtils.lerp(this.phi, this.topDownPhi, b);
			const radius = THREE.MathUtils.lerp(this.radius, Math.max(this.topDownRadius, this.radius * 2.2), b);

			this.camera.position.x = this.target.x + radius * Math.sin(theta * Math.PI / 180) * Math.cos(phi * Math.PI / 180);
			this.camera.position.y = this.target.y + radius * Math.sin(phi * Math.PI / 180);
			this.camera.position.z = this.target.z + radius * Math.cos(theta * Math.PI / 180) * Math.cos(phi * Math.PI / 180);
			this.camera.updateMatrix();
			this.camera.lookAt(this.target);
		}
	}

	public handleKeyboardEvent(event: KeyboardEvent, code: string, pressed: boolean): void
	{
		// Free camera
		if (code === 'KeyC' && pressed === true && event.shiftKey === true)
		{
			if (this.characterCaller !== undefined)
			{
				this.world.inputManager.setInputReceiver(this.characterCaller);
				this.characterCaller = undefined;
			}
		}
		else
		{
			for (const action in this.actions) {
				if (this.actions.hasOwnProperty(action)) {
					const binding = this.actions[action];
	
					if (_.includes(binding.eventCodes, code))
					{
						binding.isPressed = pressed;
					}
				}
			}
		}
	}

	public handleMouseWheel(event: WheelEvent, value: number): void
	{
		this.world.scrollTheTimeScale(value);
	}

	public handleMouseButton(event: MouseEvent, code: string, pressed: boolean): void
	{
		for (const action in this.actions) {
			if (this.actions.hasOwnProperty(action)) {
				const binding = this.actions[action];

				if (_.includes(binding.eventCodes, code))
				{
					binding.isPressed = pressed;
				}
			}
		}
	}

	public handleMouseMove(event: MouseEvent, deltaX: number, deltaY: number): void
	{
		this.move(deltaX, deltaY);
	}

	public inputReceiverInit(): void
	{
		this.target.copy(this.camera.position);
		this.setRadius(0, true);
		// this.world.dirLight.target = this.world.camera;

		this.world.updateControls([
			{
				keys: ['W', 'S', 'A', 'D'],
				desc: 'Move around'
			},
			{
				keys: ['E', 'Q'],
				desc: 'Move up / down'
			},
			{
				keys: ['Shift'],
				desc: 'Speed up'
			},
			{
				keys: ['Shift', '+', 'C'],
				desc: 'Exit free camera mode'
			},
		]);
	}

	public inputReceiverUpdate(timeStep: number): void
	{
		// Set fly speed
		let speed = this.movementSpeed * (this.actions.fast.isPressed ? timeStep * 600 : timeStep * 60);

		const up = Utils.getUp(this.camera);
		const right = Utils.getRight(this.camera);
		const forward = Utils.getBack(this.camera);

		this.upVelocity = THREE.MathUtils.lerp(this.upVelocity, +this.actions.up.isPressed - +this.actions.down.isPressed, 0.3);
		this.forwardVelocity = THREE.MathUtils.lerp(this.forwardVelocity, +this.actions.forward.isPressed - +this.actions.back.isPressed, 0.3);
		this.rightVelocity = THREE.MathUtils.lerp(this.rightVelocity, +this.actions.right.isPressed - +this.actions.left.isPressed, 0.3);

		this.target.add(up.multiplyScalar(speed * this.upVelocity));
		this.target.add(forward.multiplyScalar(speed * this.forwardVelocity));
		this.target.add(right.multiplyScalar(speed * this.rightVelocity));
	}
}