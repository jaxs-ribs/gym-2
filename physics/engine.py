import numpy as np
from tinygrad import Tensor, TinyJit
from .xpbd.broadphase import uniform_spatial_hash
from .xpbd.narrowphase import generate_contacts
from .xpbd.solver import solve_constraints
from .xpbd.velocity_solver import solve_velocities
from .xpbd.velocity_update import reconcile_velocities
from .xpbd.integration import predict_state

def _physics_step_static(x: Tensor, q: Tensor, v: Tensor, omega: Tensor, 
                         inv_mass: Tensor, inv_inertia: Tensor, shape_type: Tensor, shape_params: Tensor, friction: Tensor,
                         dt: float, gravity: Tensor, restitution: float = 0.1,
                         solver_iterations: int = 8, contact_compliance: float = 0.01) -> tuple[Tensor, Tensor, Tensor, Tensor]:
  x_old, q_old = x, q
  x_pred, q_pred, v_new, omega_new = predict_state(x, q, v, omega, inv_mass, inv_inertia, gravity, dt)
  candidate_pairs = uniform_spatial_hash(x_pred, shape_type, shape_params)
  contacts = generate_contacts(x_pred, q_pred, candidate_pairs, shape_type, shape_params, friction, contact_compliance)
  x_proj, q_proj = solve_constraints(x_pred, q_pred, contacts, inv_mass, inv_inertia, dt, iterations=solver_iterations)
  v_reconciled, omega_reconciled = reconcile_velocities(x_proj, q_proj, x_old, q_old, v_new, omega_new, dt)
  # Create dummy lambda_acc for velocity solver
  num_contacts = contacts['ids_a'].shape[0] if 'ids_a' in contacts else 0
  lambda_acc = Tensor.zeros((num_contacts,))
  v_final, omega_final = solve_velocities(v_reconciled, omega_reconciled, contacts, inv_mass, inv_inertia, dt, lambda_acc, restitution)
  return x_proj, q_proj, v_final, omega_final

def _n_step_simulation(x: Tensor, q: Tensor, v: Tensor, omega: Tensor,
                      inv_mass: Tensor, inv_inertia: Tensor, shape_type: Tensor, shape_params: Tensor, friction: Tensor,
                      dt: float, gravity: Tensor, num_steps: int, restitution: float = 0.1,
                      solver_iterations: int = 8, contact_compliance: float = 0.01) -> tuple[Tensor, Tensor, Tensor, Tensor]:
  for _ in range(num_steps):
    x, q, v, omega = _physics_step_static(x, q, v, omega, inv_mass, inv_inertia, shape_type, shape_params, friction, dt, gravity, restitution, solver_iterations, contact_compliance)
  return x, q, v, omega

class TensorPhysicsEngine:
  
  def __init__(self, x: np.ndarray, q: np.ndarray, v: np.ndarray, omega: np.ndarray,
               inv_mass: np.ndarray, inv_inertia: np.ndarray, shape_type: np.ndarray, shape_params: np.ndarray,
               friction: np.ndarray = None,
               gravity: np.ndarray = np.array([0, -9.81, 0], dtype=np.float32),
               dt: float = 0.016, restitution: float = 0.1,
               solver_iterations: int = 8, contact_compliance: float = 0.01):
    self.x = Tensor(x.astype(np.float32))
    self.q = Tensor(q.astype(np.float32))
    self.v = Tensor(v.astype(np.float32))
    self.omega = Tensor(omega.astype(np.float32))
    self.inv_mass = Tensor(inv_mass.astype(np.float32))
    self.inv_inertia = Tensor(inv_inertia.astype(np.float32))
    self.shape_type = Tensor(shape_type.astype(np.int32))
    self.shape_params = Tensor(shape_params.astype(np.float32))
    
    if friction is None:
      friction = np.ones(len(x), dtype=np.float32) * 0.5
    self.friction = Tensor(friction.astype(np.float32))
    
    self.gravity = Tensor(gravity.astype(np.float32))
    self.dt = dt
    self.restitution = restitution
    self.solver_iterations = solver_iterations
    self.contact_compliance = contact_compliance
    
    # Create JIT-compiled physics step
    self.jitted_step = TinyJit(_physics_step_static)
    self.jitted_n_step = None  # Will be created on first use
    
  def _physics_step(self) -> tuple[Tensor, Tensor, Tensor, Tensor]:
    return _physics_step_static(self.x, self.q, self.v, self.omega, 
                               self.inv_mass, self.inv_inertia, self.shape_type, self.shape_params, self.friction,
                               self.dt, self.gravity, self.restitution, self.solver_iterations, self.contact_compliance)
  
  def run_simulation(self, num_steps: int) -> None:
    # Create JIT-compiled n-step function if needed
    if self.jitted_n_step is None:
      self.jitted_n_step = TinyJit(_n_step_simulation)
    
    self.x, self.q, self.v, self.omega = self.jitted_n_step(
      self.x, self.q, self.v, self.omega, self.inv_mass, self.inv_inertia,
      self.shape_type, self.shape_params, self.friction,
      self.dt, self.gravity, num_steps, self.restitution,
      self.solver_iterations, self.contact_compliance
    )
  
  def step(self, dt: float | None = None) -> None:
    if dt is not None and dt != self.dt:
      self.dt = dt
    
    # Use JIT-compiled step
    self.x, self.q, self.v, self.omega = self.jitted_step(
      self.x, self.q, self.v, self.omega, self.inv_mass, self.inv_inertia, 
      self.shape_type, self.shape_params, self.friction,
      self.dt, self.gravity, self.restitution, self.solver_iterations, self.contact_compliance)
  
  def get_state(self) -> dict[str, np.ndarray]:
    return {
      'x': self.x.numpy(),
      'q': self.q.numpy(),
      'v': self.v.numpy(),
      'omega': self.omega.numpy(),
      'inv_mass': self.inv_mass.numpy(),
      'inv_inertia': self.inv_inertia.numpy(),
      'shape_type': self.shape_type.numpy(),
      'shape_params': self.shape_params.numpy()
    }
  
  def set_state(self, x: np.ndarray, q: np.ndarray, v: np.ndarray, omega: np.ndarray) -> None:
    self.x = Tensor(x.astype(np.float32))
    self.q = Tensor(q.astype(np.float32))
    self.v = Tensor(v.astype(np.float32))
    self.omega = Tensor(omega.astype(np.float32))

PhysicsEngine = TensorPhysicsEngine